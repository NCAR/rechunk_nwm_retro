import dask
from dask.distributed import Client, progress, LocalCluster, performance_report
from dask_jobqueue import PBSCluster
from math import ceil
import numpy as np
import numcodecs
import os
import pandas as pd
import pathlib
from rechunker import rechunk
import shutil
import socket
import subprocess
import sys
import time
import xarray as xr
import zarr

# User config
output_path = pathlib.Path("/glade/derecho/scratch/ishitas/CONUS/forcing/swdown")

# Chunk config
time_chunk_size = 672
x_chunk_size = 350
y_chunk_size = 350

n_workers = 18
n_cores = 1
queue = "casper"
cluster_mem_gb = 30

n_chunks_job = 12  # how many to do before exiting, 12 is approx yearly
end_date = '2023-01-31 23:00' # full time
# end_date = "1979-04-15 00:00"  # 1.5 months

# files
## output_path is a global, user-defined variable defined above.
os.chdir(output_path)
file_chunked = output_path / "swdown.zarr"
file_step = output_path / "step.zarr"
file_last_step = output_path / "last_step.zarr"
file_temp = output_path / "temp.zarr"
file_log_loop_time = output_path / "forcing_loop_time.txt"
file_lock = output_path / "forcing_write_in_progress.lock"

coord_file = '/glade/campaign/ral/hap/zhangyx/AORC.Forcing/1979/197902010000.LDASIN_DOMAIN1'

# input_dir = "/glade/campaign/ral/hap/zhangyx/AORC.Forcing"
input_dir = '/glade/campaign/ral/hap/zhangyx/AORC.Forcing/'
start_date = "2023-01-05 00:00"
freq = "1h"

metadata_global_add = {}
metadata_global_rm = [
    'model_initialization_time',
    'model_output_valid_time',
    'model_total_valid_times']

metadata_variable_rm = {}


def write_lock_file(file_lock, file_chunked, dates_chunk, freq):
    with open(file_lock, 'w') as ff:
        ff.write(f'file_rechunked: {str(file_chunked)}\n')
        ff.write(f'start_date: {dates_chunk[0]}\n')
        ff.write(f'end_date: {dates_chunk[-1]}\n')
        ff.write(f'freq: {freq}\n')

    assert file_lock.exists()
    return None


def rm_lock_file(file_lock):
    assert file_lock.exists()
    _ = file_lock.unlink()
    assert not file_lock.exists()
    return None


def del_zarr_file(the_file: pathlib.Path):
    if the_file.exists():
        try:
            shutil.rmtree(the_file)
            while os.path.exists(the_file):  # check if it still exists
                time.sleep(0.1)
                pass
        except:
            pass
    return None


def preprocess(ds):
    drop_vars_full = [
        "reference_time", "crs", "RAINRATE",
        'U2D', 'V2D', 'LWDOWN',
        'T2D', 'Q2D', 'PSFC',
        'LQFRAC', 'x', 'y']
    drop_vars = list(set(ds.variables).intersection(set(drop_vars_full)))
    ds = ds.drop(drop_vars) 
    if 'valid_time' in ds.variables:
        ds= (ds
             .rename({'valid_time': 'time'})
             .set_coords('time')
             .swap_dims({'Time': 'time'})
             .drop('Times')
             .rename({'south_north': 'y', 'west_east': 'x'}))
    for mm in metadata_global_rm:
        if mm in ds.attrs.keys():
            del ds.attrs[mm]
    for kk, vv in metadata_global_add.items():
        ds.attrs[kk] = vv
    for vv, ll in metadata_variable_rm.items():
        for mm in ll:
            if mm in ds.attrs.keys(): 
                del ds[vv].attrs[mm]
    return ds.reset_coords(drop=True)


def main():
    if file_lock.exists():
        raise FileExistsError(
            f'\nThe existence of the lock file:\n    {file_lock} \n'
            f'indicates that the last previous write was unsuccessful.\n'
            f'Please use the fixer script on that file.')
        return(255)

    print(f"Generate files list for all chunks in this job")
    if file_chunked.exists():
        print(f"\n ** Warning appending to existing output file: {file_chunked}")
        ds = xr.open_zarr(file_chunked)
        last_time = pd.Timestamp(ds.time.values[-1])
        ds.close()
        del ds
        dates = pd.date_range(
            start=last_time, periods=n_chunks_job * time_chunk_size + 1, freq=freq
        )[1:]
    else:
        dates = pd.date_range(
            start=start_date, periods=n_chunks_job * time_chunk_size, freq=freq
        )

    dates = dates[dates <= end_date]
    files = [
        pathlib.Path(
            f"{input_dir}/"
            f'{date.strftime("%Y")}/'
            f'{date.strftime("%Y%m%d%H%M")}.LDASIN_DOMAIN1'
        )
        for date in dates
    ]

    n_chunks_job_actual = ceil(len(files) / time_chunk_size)

    print(f"Get single file data and metadata")
    dset = xr.open_dataset(coord_file)

    print("Set cluster")
    cluster = PBSCluster(
        cores=n_cores,
        memory=f"{cluster_mem_gb}GB",
        queue=queue,
        project="NRAL0017",
        walltime="05:00:00",
        death_timeout=75,
    )
    dask.config.set({"distributed.dashboard.link": "/{port}/status"})

    print("Scale cluster")
    # cluster.adapt(maximum=n_workers, minimum=n_workers)
    cluster.scale(n_workers)

    print(f"Set client")
    client = Client(cluster)
    # print(client)
    dash_link = client.dashboard_link
    port = dash_link.split("/")[1]
    hostname = socket.gethostname()
    user = os.environ["USER"]
    print(f"Tunnel to compute node from local machine:")
    print(f"ssh -NL {port}: {hostname}:{port}{user}@cheyenne.ucar.edu")
    print(f"in local browser: ")
    print(f"http://localhost:{port}/status")
    numcodecs.blosc.use_threads = False
    # fraction of worker memory for each chunk (seems to be the max possible)
    chunk_mem_factor = 0.9
    # print(cluster.worker_spec[0]['options']['memory_limit'])
    max_mem = f"{format(chunk_mem_factor * cluster_mem_gb / n_workers, '.2f')}GB"

    indt = "    "
    for ii in range(n_chunks_job_actual):
        start_timer = time.time()

        print("\n-----------")
        print(f"ith chunk (of {n_chunks_job_actual} for this job): {ii+1}")

        istart = ii * time_chunk_size
        istop = int(np.min([(ii + 1) * time_chunk_size, len(files)]))
        dates_chunk = dates[istart:istop]
        files_chunk = files[istart:istop]
        print(f"{indt}First file: {files_chunk[0].name}")
        print(f"{indt}Last file: {files_chunk[-1].name}")

        ds = xr.open_mfdataset(
            files_chunk,
            parallel=True,
            preprocess=preprocess,
            combine="by_coords",
            concat_dim="time",
            join="override",
        )
        # print(ds)

        # in the first chunk, add back the static/invariant variables
        ds['x'] = dset['x']
        ds['y'] = dset['y']
        if not file_chunked.exists():
            ds["crs"] = dset["crs"]

        # remove the temp and step zarr datasets
        # moving these and deleting asynchornously might help speed?
        print(f"{indt}Clean up any existing temp files")
        start_del_timer = time.time()
        _ = del_zarr_file(file_temp)
        _ = del_zarr_file(file_step)
        end_del_timer = time.time()

        # the last chunk will not have a full time_chunk_size
        # handle it separately
        if len(files_chunk) == time_chunk_size:
            chunk_plan = {}
            for var in ds.data_vars:
                if len(ds[var].dims) == 3:
                    var_chunk = (time_chunk_size, y_chunk_size, x_chunk_size)
                    chunk_plan[var] = var_chunk

            print(f"{indt}chunk_plan: {chunk_plan}")

            print(f"{indt}Set rechunk_obj")
            rechunk_obj = rechunk(
                ds,
                chunk_plan,
                max_mem,
                str(file_step),
                temp_store=str(file_temp),
                executor="dask",
            )

            print(f"{indt}Execute rechunk_obj")
            with performance_report(filename="dask-report.html"):
                result = rechunk_obj.execute(retries=10)

            print(f"{indt}After rechunk_obj.execute()")

            # read back in the zarr chunk rechunker wrote
            print(f"{indt}Open zarr step file")
            ds = xr.open_zarr(str(file_step), consolidated=False)

            # Set the lock file before writing to file_chunked
            _ = write_lock_file(file_lock, file_chunked, dates_chunk, freq)

            if not file_chunked.exists():
                print(f"{indt}Write step to zarr chunked file")
                ds.to_zarr(str(file_chunked), consolidated=True, mode="w")
            else:
                print(f"{indt}Append step to zarr chunked file")
                ds.to_zarr(str(file_chunked), consolidated=True, append_dim="time")

            print(f"{indt}Close zarr chunked file")
            ds.close()

            # Remove the lock file after successful write
            _ = rm_lock_file(file_lock)

        else:
            print(f"{indt}Processing the final time chunk!")

            print(f"{indt}Clean up any existing temp files")
            start_del_timer = time.time()
            _ = del_zarr_file(file_temp)
            _ = del_zarr_file(file_step)
            _ = del_zarr_file(file_last_step)
            end_del_timer = time.time()

            print(f"{indt}Rechunking final chunk")
            last_chunk_size = len(ds.time)
            ds1 = ds.chunk(
                {"y": y_chunk_size, "x": x_chunk_size, "time": last_chunk_size})
            print(f"{indt}Writing last step file")
            _ = ds1.to_zarr(str(file_last_step), consolidated=True, mode="w")
            _ = ds1.close()

            print(f'{indt}Open last step file')
            ds2 = xr.open_zarr(str(file_last_step), consolidated=True)

            _ = write_lock_file(file_lock, file_chunked, dates_chunk, freq)
            print(f'{indt}Append last chunk to full zarr file')
            _ = ds2.to_zarr(file_chunked, consolidated=True, append_dim="time")
            _ = ds2.close()
            _ = rm_lock_file(file_lock)

            print(f"{indt}Final file clean up")
            _ = del_zarr_file(file_temp)
            _ = del_zarr_file(file_step)
            _ = del_zarr_file(file_last_step)

        # end of loop timing and logging
        end_timer = time.time()
        time_taken = end_timer - start_timer
        del_time_taken = end_del_timer - start_del_timer
        print(f"{indt}time_taken: {time_taken}")
        print(f"{indt}del_time_take: {del_time_taken}")
        cmd = (
            f"echo completed core: {n_workers*n_cores} "
            f"time_chunk_size: {time_chunk_size} "
            f"x_chunk_size: {x_chunk_size} "
            f"y_chunk_size: {y_chunk_size} "
            f"first_file: {files_chunk[0].name} "
            f"last_file: {files_chunk[-1].name} "
            f"loop_time_taken: {time_taken} "
            # f'del_time_taken: {del_time_taken} '
            f">> {file_log_loop_time}"
        )
        subprocess.run(cmd, shell=True)

    return 0


if __name__ == "__main__":
    result = main()
    sys.exit(result)
