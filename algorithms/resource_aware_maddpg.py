import torch
from torch import Tensor
from torch.autograd import Variable
import copy
from utils.neuralnets import MLPNetwork
from utils.noise import OUNoise
from utils.misc import soft_update, gumbel_softmax, onehot_from_logits

MSELoss = torch.nn.MSELoss()

class RA_MADDPG(object):
    """
    Wrapper class for DDPG-esque (i.e. also MADDPG) agents in multi-agent task
    """

    def __init__(self, in_dim, out_dim, n_agents=3, constrain_out=True, gamma=0.95, tau=0.01, lr=0.01, hidden_dim=64,
                 discrete_action=False, device='cpu'):
        """
        Inputs:
            agent_init_params (list of dict): List of dicts with parameters to
                                              initialize each agent
                num_in_pol (int): Input dimensions to policy
                num_out_pol (int): Output dimensions to policy
                num_in_critic (int): Input dimensions to critic

            gamma (float): Discount factor
            tau (float): Target update rate
            lr (float): Learning rate for policy and critic
            hidden_dim (int): Number of hidden dimensions for networks
            discrete_action (bool): Whether or not to use discrete action space
        """
        self.n_agents = n_agents
        self.control_actions = out_dim - 2
        policy_out = (self.control_actions)
        critic_in = n_agents * (in_dim + out_dim)
        self.control_policy = MLPNetwork(in_dim, policy_out, hidden_dim=hidden_dim, 
                                         discrete_action=discrete_action, constrain_out=False).to(device)
        self.options_policy = MLPNetwork(in_dim, 2, hidden_dim=hidden_dim,
                                         discrete_action=True).to(device)

        self.critic = MLPNetwork(critic_in, 1, hidden_dim=hidden_dim,
                                         discrete_action=False, constrain_out=False).to(device)

        #self.target_control_policy = copy.deepcopy(self.control_policy)
        #self.target_options_policy = copy.deepcopy(self.options_policy)
        self.target_critic = copy.deepcopy(self.critic)

        self.control_policy_optimizer = torch.optim.Adam(self.control_policy.parameters(), lr=lr)
        self.options_policy_optimizer = torch.optim.Adam(self.options_policy.parameters(), lr=lr)
        self.critic_optimizer = torch.optim.Adam(self.critic.parameters(), lr=lr)

        self.gamma = gamma
        self.tau = tau
        self.lr = lr
        self.discrete_action = discrete_action
        self.exploration = OUNoise(self.control_actions, scale=1)
        self.device = device
        self.n_iter = 0

        self.init_dict = {"lr": lr, 
                          "in_dim": in_dim,
                          "out_dim": out_dim,
                          "n_agents": n_agents,
                          "discrete_action": discrete_action,
                          "gamma": gamma, "tau": tau,}
    
    def _get_actions(self, obs):
      control = self.control_policy(obs)
      #control = control_params[:2]
      #control = (torch.randn(self.control_actions, device=self.device, requires_grad=True) * control_params[..., -2:]) + control_params[..., :-2]
      #control = torch.normal(control_params[..., :-2], torch.abs(control_params[..., -2:]))
      comm = self.options_policy(obs)
      comm = onehot_from_logits(comm)
      return torch.cat((control, comm), dim=-1)

    def _get_target_actions(self, obs):
      control_params = self.target_control_policy(obs)
      control = (torch.randn(self.control_actions, device=self.device, requires_grad=True) * control_params[..., -2:]) + control_params[..., :-2]
      #control = torch.normal(control_params[..., :-2], torch.abs(control_params[..., -2:]))
      comm = self.target_options_policy(obs)
      comm = onehot_from_logits(comm)
      return torch.cat((control, comm), dim=-1)

    def scale_noise(self, scale):
        """
        Scale noise for each agent
        Inputs:
            scale (float): scale of noise
        """
    def reset_noise(self):
      self.exploration.reset()

    def step(self, observations, explore=False):
      """
      Take a step forward in environment with all agents
      Inputs:
          observations: List of observations for each agent
          explore (boolean): Whether or not to add exploration noise
      Outputs:
          actions: List of actions for each agent
      """
      actions = []
      observations = observations.squeeze()
      for obs in observations:
        action = self._get_actions(obs).to('cpu')
        cont = action[:2]
        discrete = action[2:]

        if explore:
          noise = Variable(Tensor(self.exploration.noise()),
                                  requires_grad=False)
          cont = cont + noise
          discrete = gumbel_softmax(discrete.unsqueeze(0), hard=True).squeeze()
        else:
          discrete = onehot_from_logits(discrete)

        action = torch.cat((cont, discrete), dim=0)
        action = action.clamp(-1, 1)
        actions.append(action.detach())

      return actions

    def update(self, sample, agent_i, parallel=False, logger=None):
        """
        Update parameters of agent model based on sample from replay buffer
        Inputs:
            sample: tuple of (observations, actions, rewards, next
                    observations, and episode end masks) sampled randomly from
                    the replay buffer. Each is a list with entries
                    corresponding to each agent
            agent_i (int): index of agent to update
            parallel (bool): If true, will average gradients across threads
            logger (SummaryWriter from Tensorboard-Pytorch):
                If passed in, important quantities will be logged
        """
        obs, acs, rews, next_obs, dones = sample

        # Critic loss

        all_trgt_acs = [self._get_actions(nobs) for nobs in next_obs]
        trgt_vf_in = torch.cat((*next_obs, *all_trgt_acs), dim=1)
        target_value = (rews[agent_i].view(-1, 1) + self.gamma *
                        self.target_critic(trgt_vf_in) *
                        (1 - dones[agent_i].view(-1, 1)))

        vf_in = torch.cat((*obs, *acs), dim=1)
        actual_value = self.critic(vf_in)
        vf_loss = MSELoss(actual_value, target_value.detach())

        self.critic_optimizer.zero_grad()
        vf_loss.backward()

        torch.nn.utils.clip_grad_norm_(self.critic.parameters(), 0.5)
        self.critic_optimizer.step()

        self.control_policy_optimizer.zero_grad()
        self.options_policy_optimizer.zero_grad()

        curr_acs= self._get_actions(obs[agent_i])

        all_acs = []
        for i, ob in zip(range(self.n_agents), obs):
            if i == agent_i:
                all_acs.append(curr_acs)
            else:
                all_acs.append(self._get_actions(ob).detach())

        vf_in = torch.cat((*obs, *all_acs), dim=1)

        pol_loss = -self.critic(vf_in).mean()
        pol_loss += (curr_acs**2).mean() * 1e-3
        pol_loss.backward()

        torch.nn.utils.clip_grad_norm_(self.control_policy.parameters(), 0.5)
        torch.nn.utils.clip_grad_norm_(self.options_policy.parameters(), 0.5)
        
        self.control_policy_optimizer.step()
        self.options_policy_optimizer.step()

        if logger is not None:
            logger.add_scalars('agent/losses',
                               {'vf_loss': vf_loss,
                                'pol_loss': pol_loss},
                               self.n_iter)

    def to_device(self, device):
      self.device = device
      self.control_policy.to(device)
      self.options_policy.to(device)
      self.critic.to(device)

      #self.target_control_policy.to(device)
      #self.target_options_policy.to(device)
      self.target_critic.to(device)

    def update_all_targets(self):
        """
        Update all target networks (called after normal updates have been
        performed for each agent)
        """
        soft_update(self.target_critic, self.critic, self.tau)
        #soft_update(self.target_control_policy, self.control_policy, self.tau)
        #soft_update(self.target_options_policy, self.options_policy, self.tau)
        self.n_iter += 1

    def save(self, filename):
        """
        Save trained parameters of all agents into one file
        """
        # self.prep_training(
        # device='cpu')  # move parameters to CPU before saving
        save_dict = {'init_dict': self.init_dict,
                    "control_policy": self.control_policy.state_dict(),
                    "options_policy": self.options_policy.state_dict(),
                    "critic": self.critic.state_dict(),
                    "n_iter": self.n_iter,

                    "control_optimizer": self.control_policy_optimizer.state_dict(),
                    "options_optimizer": self.options_policy_optimizer.state_dict(),
                    "critic_optimizer": self.critic_optimizer.state_dict(),
                    }
        torch.save(save_dict, filename)

    @classmethod
    def init_from_save(cls, filename, device='cuda'):
        """
        Instantiate instance of this class from file created by 'save' method
        """
        device = torch.device(device)
        save_dict = torch.load(filename, map_location=device)
        instance = cls(**save_dict['init_dict'])
        instance.init_dict = save_dict['init_dict']
        instance.control_policy.load_state_dict = save_dict["control_policy"]
        instance.options_policy.load_state_dict = save_dict["options_policy"]
        instance.critic.load_state_dict = save_dict["critic"]

        instance.control_policy_optimizer.load_state_dict = save_dict["control_optimizer"]
        instance.options_policy_optimizer.load_state_dict = save_dict["options_optimizer"]
        instance.critic_optimizer.load_state_dict = save_dict["critic_optimizer"]
        instance.device = device

        instance.n_iter = save_dict["n_iter"]
        instance.to_device(device)

        return instance