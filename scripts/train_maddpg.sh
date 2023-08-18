#!/bin/sh
#SBATCH -A uppmax2023-2-14
#SBATCH -M snowy
#SBATCH -p core
#SBATCH -n 8
#SBATCH -t 48:00:00
#SBATCH -J "ra_maddpg"
#SBATCH -o /home/pagliaro/project/RAC/outs/%j.out

PYTHON=/proj/uppmax2023-2-14/envs/RAC/bin/python3

module load conda
conda activate RAC
cd /home/pagliaro/project/RAC

$PYTHON scripts/train_maddpg.py -n 4 --experiment_name validation_uppmax
