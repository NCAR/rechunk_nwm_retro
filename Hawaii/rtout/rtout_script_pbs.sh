#!/bin/bash -l
#PBS -N rtout
#PBS -A NRAL0017
#PBS -l select=1:ncpus=1:mem=10GB
#PBS -l walltime=05:00:00
#PBS -q casper
#PBS -j oe
#PBS -k oed

export TMPDIR=/glade/scratch/$USER/temp
mkdir -p $TMPDIR

source /glade/work/ishitas/python_envs/py_casper/bin/activate

python /glade/work/arezoo/HAWAII_PR/rechunk_retro_nwm_v21_hawaii/rtout/rtout_to_zarr.py > /glade/scratch/ishitas/HI/rtout/rtout_log

exit 0
