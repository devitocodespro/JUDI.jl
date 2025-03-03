# 2D FWI on Overthrust model with SPG using minConf library
# Author: Philipp Witte, pwitte@eoas.ubc.ca
# Date: December 2017
#

using Statistics, Random, LinearAlgebra, PythonPlot
using JUDI, SlimOptim, HDF5, SegyIO, SlimPlotting

# Load starting model
n,d,o,m0 = read(h5open("$(JUDI.JUDI_DATA)/overthrust_model.h5","r"), "n", "d", "o", "m0")
model0 = Model((n[1],n[2]), (d[1],d[2]), (o[1],o[2]), m0)

# Bound constraints
v0 = sqrt.(1f0 ./ m0)
vmin = ones(Float32,model0.n) .* 1.3f0
vmax = ones(Float32,model0.n) .* 6.5f0
vmin[:,1:21] .= v0[:,1:21]   # keep water column fixed
vmax[:,1:21] .= v0[:,1:21]

# Slowness squared [s^2/km^2]
mmin = vec((1f0 ./ vmax).^2)
mmax = vec((1f0 ./ vmin).^2)

# Load data
block = segy_read("$(JUDI.JUDI_DATA)/overthrust_shot_records.segy")
d_obs = judiVector(block)

# Set up wavelet
src_geometry = Geometry(block; key="source")
wavelet = ricker_wavelet(src_geometry.t[1], src_geometry.dt[1], 0.008f0)  # 8 Hz wavelet
q = judiVector(src_geometry,wavelet)

############################### FWI ###########################################
F0 = judiModeling(deepcopy(model0), src_geometry, d_obs.geometry)

# Optimization parameters
fevals = parse(Int, get(ENV, "NITER", "10"))
batchsize = 8

# Objective function for minConf library
count = 0
function objective_function(x)
    model0.m .= reshape(x,model0.n);

    # fwi function value and gradient
    i = randperm(d_obs.nsrc)[1:batchsize]
    fval, grad = fwi_objective(model0, q[i], d_obs[i])
    grad = .125f0*grad/maximum(abs.(grad))  # scale for line search

    global count; count+= 1
    return fval, grad
end

# Bound projection
proj(x) = reshape(median([vec(mmin) vec(x) vec(mmax)]; dims=2),model0.n)

# FWI with SPG
options = spg_options(verbose=3, maxIter=fevals, memory=3)
solf32 = spg(objective_function, v0.^(-2), proj, options)
vf32 = sqrt.(1f0 ./ solf32.x)


JUDI.pm._dtypes["params"] = "f16"
JUDI.pm._dtypes["fields"] = "f16"
solf16 = spg(objective_function, v0.^(-2), proj, options)
vf16 = sqrt.(1f0 ./ solf16.x)

# Plot result

figure(figsize=(10,6))
subplot(121)
plot_velocity(vf32', model0.d; name="Float32", new_fig=false)
subplot(122)
plot_velocity(vf16', model0.d; name="Float16", new_fig=false)
tight_layout()


figure()
plot(solf32.ϕ_trace; label="Float32")
plot(solf16.ϕ_trace; label="Float16")
xlabel("Iteration")
ylabel("Objective function value")
legend()
tight_layout()