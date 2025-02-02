import sys, time
import numpy as np

distribution = sys.argv[1]
duration = float(sys.argv[2])
instances = int(sys.argv[3])

# https://arxiv.org/pdf/1607.05356.pdf
# scale is 1/lamda, or target time between requests
def poisson_times(n, rng, scale=1.0, t0=0.0, start=0.0, end=60.0):
    t = t0
    delays = rng.exponential(scale=scale, size=n)
    times = []
    for d in delays:
        t += d
        times.append(t)
    times = np.array(times)
    return times * (end/times.max()) + start

def uniform_times(n, rng, start=0.0, end=60.0):
    return rng.uniform(low=start, high=end, size=n)

def gamma_times(n, rng, shape=2.0, start=0.0, end=60.0):
    times = rng.gamma(shape, size=n)
    return times * (end/times.max()) + start

def normal_times(n, rng, mean=0.0, stddev=0.2, start=0.0, end=60.0):
    times = rng.normal(mean, stddev, n)
    times = times - times.min()
    return times * (end/times.max()) + start

def bimodal_times(n, rng, mean1=-1.0, mean2=1.0, stddev=0.3, start=0.0, end=60.0):
    times1 = rng.normal(mean1, stddev, round(n/2))
    times2 = rng.normal(mean2, stddev, round(n/2))
    times = np.concatenate((times1, times2))
    times = times - times.min()
    return times * (end/times.max()) + start

def serialize(a):
    return ",".join(np.char.mod("%f", a))


rng = np.random.default_rng(123456789)

times = ""

if distribution == "poisson":
    times = serialize(poisson_times(instances, rng, end=duration))
elif distribution == "uniform":
    times = serialize(uniform_times(instances, rng, end=duration))
elif distribution == "gamma":
    times = serialize(gamma_times(instances, rng, end=duration))
elif distribution == "normal":
    times = serialize(normal_times(instances, rng, end=duration))
elif distribution == "bimodal":
    times = serialize(bimodal_times(instances, rng, end=duration))
else:
    print("unknown distribution")
    sys.exit(1)

print(times)
