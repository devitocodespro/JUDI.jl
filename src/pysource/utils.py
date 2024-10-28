try:
    from collections.abc import Iterable
except ImportError:
    from collections import Iterable

import os
import numpy as np
from sympy import sqrt

from devito import configuration
from devito.arch import Device
from devito.arch.compiler import NvidiaCompiler, CudaCompiler
from devito.tools import as_tuple

try:
    import devitopro as dvp
except ImportError:
    dvp = None


# Weighting
def weight_fun(w_fun, model, src_coords):
    """
    Symbolic weighting function

    Parameters
    ----------
    w_fun: Tuple(String, Float)
        Weighting fucntion and weight.
    model: Model
        Model structure
    src_coords: Array
        Source coordinates.
    """
    if w_fun is None:
        return 1
    else:
        return weight_srcfocus(model, src_coords, delta=w_fun[1],
                               full=(w_fun[0] == "srcfocus"))


def weight_srcfocus(model, src_coords, delta=.01, full=True):
    """
    Source focusing weighting function
    w(x) = sqrt((||x-xsrc||^2+delta^2)/delta^2)

    Parameters
    ----------
    model: Model
        Model structure
    src_coords: Array
        Source coordinates
    delta: Float
        Reference distance for weights
    """
    w_dim = as_tuple(model.grid.dimensions if full else model.grid.dimensions[-1])
    isrc = tuple(np.float32(model.padsizes[i][0]) + src_coords[0, i] / model.spacing[i]
                 for i in range(model.dim))
    h = np.prod(model.spacing)**(1/model.dim)
    radius = sum((d - isrc[i])**2 for i, d in enumerate(w_dim))
    return sqrt(radius + (delta / h)**2) / (delta/h)


def compute_optalpha(norm_r, norm_Fty, epsilon, comp_alpha=True):
    """
    Compute optimal alpha for WRI

    Parameters
    ----------
    norm_r: Float
        Norm of residual
    norm_Fty: Float
        Norm of adjoint wavefield squared
    epsilon: Float
        Noise level
    comp_alpha: Bool
        Whether to compute the optimal alpha or just return 1
    """
    if comp_alpha:
        if norm_r > epsilon and norm_Fty > 0:
            return norm_r * (norm_r - epsilon) / norm_Fty
        else:
            return 0
    else:
        return 1


def opt_op(model):
    """
    Setup the compiler options for the operator. Dependeing on the devito
    version more or less options can be used for performance, mostly impacting TTI.

    Parameters
    ----------
    model: Model
        Model structure to know if we are in a TTI model
    """
    if isinstance(configuration['platform'], Device):
        opts = {'openmp': True if configuration['language'] == 'openmp' else None,
                'mpi': configuration['mpi']}
        mode = 'advanced'
    else:
        opts = {'openmp': True, 'par-collapse-ncores': 2, 'mpi': configuration['mpi']}
        mode = 'advanced'
    return (mode, opts)


def nfreq(freq_list):
    """
    Check number of on-the-fly DFT frequencies.
    """
    return 0 if freq_list is None else np.shape(freq_list)[0]


def fields_kwargs(*args):
    """
    Creates a dictionary of {f.name: f} for any field argument that is not None
    """
    kw = {}
    for field in args:
        if field is not None:
            # In some case could be a tuple of fields, such as dft modes
            if isinstance(field, Iterable):
                kw.update(fields_kwargs(*field))
            else:
                try:
                    kw.update({f.name: f for f in field.flat()})
                    continue
                except AttributeError:
                    kw.update({field.name: field})

    return kw


def base_kwargs(dt):
    """
    Most basic keyword arguments needed by the operator.
    """
    return {'dt': dt}


def cleanup_wf(u):
    """
    Delete serialized snapshots
    """
    for ui in as_tuple(u):
        try:
            serialized = ui._parent._fnames
            basedir = '/'.join(str(serialized[0]).split('/')[:-1])
            os.system('rm -rf %s' % basedir)
        except AttributeError:
            continue


def compression_mode():
    """
    Check compiler used to see if can use bitcomp
    """
    if isinstance(configuration['compiler'], (NvidiaCompiler, CudaCompiler)):
        return 'bitcomp'
    else:
        return 'noop'


def npdot(a, b):
    """
    Inner product of n-dimensional ndarrays through numpy einsum
    """
    inds = 'ijklm'[:len(a.shape)]
    return np.einsum('%s,%s->' % (inds, inds), a, b)
