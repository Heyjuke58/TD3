import numpy as np
from typing import Any
import torch
import gym
import argparse
import os
from time import perf_counter
import csv

import TD3.utils as utils
from TD3.TD3 import TD3
from TD3.OurDDPG import DDPG as OurDDPG
from TD3.DDPG import DDPG
from src.utils import set_seeds, get_timestamp


def write_eval_to_csv(
    res_file: str, avg_reward: float, time: float, env_steps: int, grad_steps: int, seed: int
):
    with open(res_file, "a") as csv_f:
        writer = csv.writer(csv_f, delimiter=",")
        writer.writerow([avg_reward, time, env_steps, grad_steps, seed])


# Runs policy for X episodes and returns average reward
# A fixed seed is used for the eval environment
def eval_policy(policy, env_name, seed, eval_episodes=10):
    eval_env = gym.make(env_name)
    eval_env.seed(seed + 100)

    avg_reward = 0.0
    for _ in range(eval_episodes):
        state, done = eval_env.reset(), False
        while not done:
            action = policy.select_action(np.array(state))
            state, reward, done, _ = eval_env.step(action)
            avg_reward += reward

    avg_reward /= eval_episodes

    print("---------------------------------------")
    print(f"Evaluation over {eval_episodes} episodes: {avg_reward:.3f}")
    print("---------------------------------------")
    return avg_reward


def parse_args() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="TD3")  # Policy name (TD3, DDPG or OurDDPG)
    parser.add_argument("--env", default="HalfCheetah-v2")  # OpenAI gym environment name
    parser.add_argument("--seed", default=0, type=int)  # Sets Gym, PyTorch and Numpy seeds
    parser.add_argument(
        "--start_timesteps", default=25e3, type=int
    )  # Time steps initial random policy is used
    parser.add_argument("--eval_freq", default=5e3, type=int)  # How often (time steps) we evaluate
    parser.add_argument(
        "--max_timesteps", default=1e6, type=int
    )  # Max time steps to run environment
    parser.add_argument("--expl_noise", default=0.1)  # Std of Gaussian exploration noise
    parser.add_argument(
        "--batch_size", default=256, type=int
    )  # Batch size for both actor and critic
    parser.add_argument("--discount", default=0.99)  # Discount factor
    parser.add_argument("--tau", default=0.005)  # Target network update rate
    parser.add_argument(
        "--policy_noise", default=0.2
    )  # Noise added to target policy during critic update
    parser.add_argument("--noise_clip", default=0.5)  # Range to clip target policy noise
    parser.add_argument("--policy_freq", default=2, type=int)  # Frequency of delayed policy updates
    parser.add_argument("--save_model", action="store_true")  # Save model and optimizer parameters
    parser.add_argument(
        "--load_model", default=""
    )  # Model load file name, "" doesn't load, "default" uses file_name
    parser.add_argument("--dest_model_path", type=str, dest="dest_model_path", default="./models")
    parser.add_argument("--dest_res_path", type=str, dest="dest_res_path", default="./results")
    args = parser.parse_args()
    return vars(args)


def main(dargs: dict[str, Any]):
    # args are converted back to namespace (better for calling the main with kwargs,
    # which is more in-line with our SAC implementation.
    args = argparse.Namespace(**dargs)

    # set up file and folders for saving stats:
    # file_name = f"{args.policy}_{args.env}_{get_timestamp()}"
    print("---------------------------------------")
    print(f"Policy: {args.policy}, Env: {args.env}, Seed: {args.seed}")
    print("---------------------------------------")

    if not os.path.exists(args.dest_res_path):
        os.makedirs(args.dest_res_path)
    if args.save_model and not os.path.exists(args.dest_model_path):
        os.makedirs(args.dest_model_path)

    res_file = f"{args.dest_res_path}/{args.file_name}.csv"
    if not os.path.exists(res_file):
        # create results csv and write header and hyperpars to it
        with open(res_file, "x") as csv_f:
            # TODO: write hyperpars in first lines
            hyperpars_str = (
                "Hyperparameters\n"
                f"Env: {args.env}\n"
                f"Seed: {args.seed}\n"
                f"Eval frequency: {args.eval_freq}\n"
                f"Number of initial exploration steps: {args.start_timesteps}\n"
                f"Max env steps: {args.max_timesteps}\n"
                f"Batch size: {args.batch_size}\n"
                f"Discount factor: {args.discount}\n"
                f"Target network update rate: {args.tau}\n"
                f"Policy noise: {args.policy_noise}\n"
                f"Noise clip: {args.noise_clip}\n"
                f"Frequency of delayed policy updates: {args.policy_freq}\n\n"
            )
            csv_f.write(hyperpars_str)
            csv_f.write("avg_reward,time,env_steps,grad_steps,seed\n")

    # set up environment and actor:
    env = gym.make(args.env)

    # Set seeds
    # env.seed(args.seed)
    # env.action_space.seed(args.seed)
    # torch.manual_seed(args.seed)
    # np.random.seed(args.seed)
    set_seeds(args.seed, env)

    state_dim = env.observation_space.shape[0]
    action_dim = env.action_space.shape[0]
    max_action = float(env.action_space.high[0])

    kwargs = {
        "state_dim": state_dim,
        "action_dim": action_dim,
        "max_action": max_action,
        "discount": args.discount,
        "tau": args.tau,
    }

    # Initialize policy
    if args.policy == "TD3":
        # Target policy smoothing is scaled wrt the action scale
        kwargs["policy_noise"] = args.policy_noise * max_action
        kwargs["noise_clip"] = args.noise_clip * max_action
        kwargs["policy_freq"] = args.policy_freq
        policy = TD3(**kwargs)
    elif args.policy == "OurDDPG":
        policy = OurDDPG(**kwargs)
    elif args.policy == "DDPG":
        policy = DDPG(**kwargs)

    if args.load_model != "":
        policy_file = args.file_name if args.load_model == "default" else args.load_model
        policy.load(f"./models/{policy_file}")

    replay_buffer = utils.ReplayBuffer(state_dim, action_dim)

    # Evaluate untrained policy
    avg_reward = eval_policy(policy, args.env, args.seed)
    write_eval_to_csv(res_file, avg_reward, 0, 0, 0, args.seed)

    state, done = env.reset(), False
    episode_reward = 0
    episode_timesteps = 0
    episode_num = 0
    grad_steps = 0

    start_time = perf_counter()
    for t in range(int(args.max_timesteps)):

        episode_timesteps += 1

        # Select action randomly or according to policy
        if t < args.start_timesteps:
            action = env.action_space.sample()
        else:
            action = (
                policy.select_action(np.array(state))
                + np.random.normal(0, max_action * args.expl_noise, size=action_dim)
            ).clip(-max_action, max_action)

        # Perform action
        next_state, reward, done, _ = env.step(action)
        done_bool = float(done) if episode_timesteps < env._max_episode_steps else 0

        # Store data in replay buffer
        replay_buffer.add(state, action, next_state, reward, done_bool)

        state = next_state
        episode_reward += reward

        # Train agent after collecting sufficient data
        if t >= args.start_timesteps:
            policy.train(replay_buffer, args.batch_size)
            grad_steps += 1

        if done:
            # +1 to account for 0 indexing. +0 on ep_timesteps since it will increment +1 even if done=True
            print(
                f"Total T: {t+1} Episode Num: {episode_num+1} Episode T: {episode_timesteps} Reward: {episode_reward:.3f}"
            )
            # Reset environment
            state, done = env.reset(), False
            episode_reward = 0
            episode_timesteps = 0
            episode_num += 1

        # Evaluate episode
        if (t + 1) % args.eval_freq == 0:
            elapsed_time = perf_counter() - start_time
            start_time_eval = perf_counter()
            avg_reward = eval_policy(policy, args.env, args.seed)
            write_eval_to_csv(
                res_file,
                avg_reward,
                elapsed_time,
                t + 1,
                grad_steps,
                args.seed,
            )

            # evaluations.append(eval_policy(policy, args.env, args.seed))
            # np.save(f"{args.dest_res_path}/{file_name}", evaluations)
            if args.save_model:
                policy.save(f"{args.dest_model_path}/{args.file_name}")

            # ignore time for evaluation by adding it to start time
            start_time += perf_counter() - start_time_eval

    # Evaluate final result
    # elapsed_time = perf_counter() - start_time
    # avg_reward = eval_policy(policy, args.env, args.seed)
    # write_eval_to_csv(
    #     res_file,
    #     avg_reward,
    #     elapsed_time,
    #     int(args.max_timesteps),
    #     grad_steps,
    #     args.seed,
    # )
    # evaluations.append(eval_policy(policy, args.env, args.seed))
    # np.save(f"{args.dest_res_path}/{file_name}", evaluations)
    if args.save_model:
        policy.save(f"{args.dest_model_path}/{args.file_name}")


if __name__ == "__main__":
    args = parse_args()
    main(args)
