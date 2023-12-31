#!/bin/bash -l
#PBS -N ldasout_hi
#PBS -A NRAL0017
#PBS -l select=1:ncpus=1:mem=10GB
#PBS -l walltime=02:00:00
#PBS -q casper
#PBS -j oe

export TMPDIR=/glade/scratch/$USER/temp
mkdir -p $TMPDIR

module load ncarenv python/3.7.9
source /glade/work/ishitas/python_envs/379_demo/bin/activate

python /glade/work/arezoo/HAWAII_PR/rechunk_retro_nwm_v21_hawaii/ldasout/ldasout_to_zarr.py

exit 0
