#!/bin/sh
#SBATCH -A uppmax2023-2-14
#SBATCH -M snowy
#SBATCH -p node 
#SBATCH -n 1
#SBATCH --gpus=1
#SBATCH --gpus-per-node=1
#SBATCH -t 24:00:00
#SBATCH -J "mappo_gpu"
#SBATCH -o /home/pagliaro/project/RAC/outs/%j.out

PYTHON=/proj/uppmax2023-2-14/envs/RAC/bin/python3

env="MPE"
scenario="simple_spread" 

module load conda
conda activate RAC
cd /home/pagliaro/project/RAC

$PYTHON scripts/train_mappo.py --env_name ${env}\
    --scenario_name ${scenario} --ppo_epoch 15 --critic_hidden_size 256 --actor_hidden_size 256 \
 		--lr 3e-5 --layer_N 1 --gamma 0.95 --critic_lr 4e-5 --clip_param 0.3 --gae_lambda 0.98 \
		--use_ReLU --algorithm_name rmappo --com_ratio 0 \
    --n_training_threads 2 --n_rollout_threads 14 --num_mini_batch 1 \
		--episode_length 25 --num_env_steps 10000000 --n_trajectories 5 $@
