import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, diags


"""def records_to_csr(N, records, lamb):
    row, col, t, value = zip(*records)
    data = lamb*np.array(value)*np.ones_like(row)
    data = np.log(1-data)
    return csr_matrix((data, (row, col)), shape=(N, N))"""

def contacts_rec_to_csr(N, records, lamb, log1m=False):
    """
    Faster function to take advantage of records array
    and avoid loops
    @author: Fabio Mazza
    """
    #print(type(records))
    if isinstance(records, np.recarray):
        records.dtype.names = "i","j","t","m"
        row = records["i"]
        col = records["j"]
        value = records["m"]
    else:
        rec = np.array(records)
        row = rec[:,0].astype(int)
        col = rec[:,1].astype(int)
        value = rec[:,2].astype(float)
    data = lamb*np.array(value)*np.ones_like(row)
    if log1m:
        data = np.log(1-data)
    return csr_matrix((data, (row, col)), shape=(N, N))

def contacts_to_csr(N, contacts, lamb):
    if len(contacts) == 0:
        return csr_matrix((N, N))
    else:
        return contacts_rec_to_csr(N, contacts, lamb)


'''def get_notinf_p_mean_field(probas, lambs):
    """ 
    compute probability of not being infected
    """
    pi=probas[:,1]
    pim = diags(pi)
    r = pim.dot(lambs)*(-1)
    p_noti=np.exp(r.log1p().sum(0)).A1
    #notinf_p = np.exp(nus.dot(probas[:, 1]))
    assert np.all((p_noti >=0) & (p_noti <=1))
    return p_noti
'''
from .fast_nb import calc_loop_p_nb
def get_notinf_p_mean_field(probas, lambs):
    pnoti = calc_loop_p_nb(probas[:,1], lambs)
    if not np.all((pnoti >=0) & (pnoti <=1)):
        x=np.where(np.any((pnoti <0) | (pnoti >1)))
        print(x, pnoti[x], )
        ii=lambs[:,x[0]].nonzero()
        print("Lambs i:" , ii, lambs[ii[0],ii[1]])
        #print(probas[])
        raise AssertionError
    return pnoti


def propagate(probas, not_inf_probs, recover_probas):
    """
    - probas[i,s] = P_s^i(t)
    - not_inf_probs[i]  = proba that i doesn't get infected (if susceptible)
    - recover_probas[i] = proba that i recovers (if infected)
    - probas_next[i, s] = P_s^i(t+1)
    """
    probas_next = np.zeros_like(probas)
    probas_next[:, 0] = probas[:, 0]*not_inf_probs
    probas_next[:, 1] = probas[:, 1]*(1 - recover_probas) + probas[:, 0]*(1-not_inf_probs)
    probas_next[:, 2] = probas[:, 2] + probas[:, 1]*recover_probas
    ## try fixing probs
    check = probas_next > 1
    fix = probas_next[check] -1 < 1e-15
    
    assert np.all((probas_next >=0)) and np.all(fix)
    #if len(fix)>0:
    #    id_fix = np.stack(np.where(check))[fix]
    #    probas_next[id_fix[0],id_fix[1]] = 1
    return probas_next


def reset_probas(t, probas, observations):
    """
    Reset probas[t] according to observations
    - observations = list of dict(i=i, s=s, t=t) observations at t_obs=t
    If s=I, the observation must also give t_I the infection time
    - probas[t, i, s] = P_s^i(t)
    """
    for obs in observations:
        if (obs["s"] == 0) and (t <= obs["t"]):
            probas[t, obs["i"], :] = [1., 0., 0.]  # p_i^S = 1
        if (obs["s"] == 1) and (obs["t_I"] <= t) and (t <= obs["t"]):
            probas[t, obs["i"], :] = [0., 1., 0]  # p_i^I = 1
        if (obs["s"] == 2) and (t >= obs["t"]):
            probas[t, obs["i"], :] = [0., 0., 1.]  # p_i^R = 1


def run_mean_field(initial_probas, recover_probas, transm, observations):
    """
    Run the probability evolution from t=0 to t=t_max=len(transmissions) and:
    - recover_probas[i] = mu_i time-independent
    - transmissions[t] = csr sparse matrix of i, j, lambda_ij(t)
    - observations = list of dict(i=i, s=s, t=t) observations at t_obs=t
    If s=I the observation must also give t_I the infection time
    - probas[t, i, s] = P_s^i(t)
    """
    # initialize
    t_max = len(transm)
    N = initial_probas.shape[0]
    probas = np.zeros((t_max + 1, N, 3))
    probas[0] = initial_probas.copy()
    # iterate over time steps
    for t in range(t_max):
        reset_probas(t, probas, observations)
        notinf_probas = get_notinf_p_mean_field(
            probas[t], transm[t]
        )
        probas[t+1] = propagate(
            probas[t], notinf_probas, recover_probas
        )
        if np.any(probas[t+1] < 0):
            raise AssertionError("Probas are negative")
    return probas


def ranking_backtrack(t, transmiss, observations, delta, tau, mu, rng):
    """Backtrack using mean field.

    Run mean field from t - delta to t, starting from all susceptible and
    resetting the probas according to the observations. For all observations,
    we assume the time of infection is t_I = t_obs - tau. The recovery proba is
    mu for all individuals.

    Returns scores = probas[s=I, t=t]. If t < delta returns random scores.
    """
    N = transmiss[0].shape[0]
    if (t < delta):
        scores = rng.rand(N)/N
        return scores
    t_start = t - delta
    initial_probas = np.broadcast_to([1.,0.,0.], (N, 3)) # all susceptible start
    recover_probas = mu*np.ones(N)
    # shift by t_start
    for obs in observations:
        obs["t"] = obs["t_test"] - t_start
        obs["t_I"] = obs["t"] - tau
    probas = run_mean_field(
        initial_probas, recover_probas, transmiss[t_start:t+1], observations
    )
    scores = probas[t-t_start, :, 1].copy()  # probas[s=I, t]
    if sum(scores) < 0:
        raise ValueError("Negative sum of scores")
    return scores


def make_tie_break(rng=None):
    if rng is None:
        rng = np.random
    return lambda t: (t[1], rng.rand())


def get_rank(scores, tie_break):
    """
    Returns list of (index, value) of scores, sorted by decreasing order.
    The order is randomized in case of tie thanks to the tie_break function.
    """
    return sorted(enumerate(scores), key=tie_break, reverse=True)


def check_inputs(t_day, daily_contacts, daily_obs):
    t_min = min(t for i, j, t, lamb in daily_contacts)
    t_max = max(t for i, j, t, lamb in daily_contacts)
    if (t_min != t_max) or (t_min != t_day):
        raise ValueError(
            f"daily_contacts t_min={t_min} t_max={t_max} t_day={t_day}"
        )
    if daily_obs:
        t_min = min(t for i, s, t in daily_obs)
        t_max = max(t for i, s, t in daily_obs)
        if (t_min != t_max) or (t_min != t_day-1):
            raise ValueError(
                f"daily_obs t_min={t_min} t_max={t_max} t_day-1={t_day-1}"
            )
    return

def prepare_obs(daily_obs):
    return [
            dict(i=i, s=s, t_test=t_test) for i, s, t_test in daily_obs
        ]


class MeanFieldRanker:

    def __init__(self, tau, delta, mu, lamb):
        self.description = "class for mean field inference of openABM loop"
        self.author = "https://github.com/sphinxteam"
        self.tau = tau
        self.delta_init = delta
        self.mu = mu
        self.lamb = lamb
        self.rng = np.random.RandomState(1)
        self._tie = make_tie_break(self.rng)

    def init(self, N, T):
        self.transmissions = []
        self.observations = []
        self.T = T
        self.N = N
        self.mfIs = np.full(T, np.nan)

        return True

    def _append_data(self, t_day, daily_contacts, daily_obs):
        """
        Add obs
        """
        # check that t=t_day in daily_contacts and t=t_day-1 in daily_obs
        #check_inputs(t_day, daily_contacts, daily_obs)
        # append daily_contacts and daily_obs
        if len(daily_contacts) > 0:
            daily_transmissions = contacts_rec_to_csr(self.N, daily_contacts, self.lamb, log1m=False)
        else:
            daily_transmissions = csr_matrix((self.N, self.N))
        self.transmissions.append(daily_transmissions)
        self.observations += [
            dict(i=i, s=s, t_test=t_test) for i, s, t_test in daily_obs
        ]

    def rank(self, t_day, daily_contacts, daily_obs, data):
        '''
        computing rank of infected individuals
        return: list -- [(index, value), ...]
        '''
        self.delta = min(self.delta_init, t_day)
        
        self._append_data(t_day, daily_contacts, daily_obs)

        # scores given by mean field run from t-delta to t
        scores = ranking_backtrack(
            t_day, self.transmissions, self.observations,
            self.delta, self.tau, self.mu, self.rng,
        )
        self.mfIs[t_day] = sum(scores)
        data["<I>"] = self.mfIs        
        # convert to list [(index, value), ...]
        rank = get_rank(scores, self._tie)
        return rank
