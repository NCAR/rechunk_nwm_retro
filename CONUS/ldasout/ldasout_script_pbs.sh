#!/bin/bash -l
#PBS -N ldasout
#PBS -A NRAL0017
#PBS -l select=1:ncpus=1:mem=10GB
#PBS -l walltime=02:00:00
#PBS -q casper
#PBS -j oe

export TMPDIR=/glade/scratch/$USER/temp
mkdir -p $TMPDIR

source /glade/work/ishitas/python_envs/py_casper_new/bin/activate

python /glade/work/ishitas/CONUS_Retro_Run/rechunk_retro_nwm_v21/ldasout/ldasout_to_zarr.py

exit 0
