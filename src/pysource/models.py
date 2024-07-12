import numpy as np
import warnings
from functools import cached_property

from sympy import finite_diff_weights as fd_w
from devito import (Grid, Function, SubDimension, Eq, Inc, switchconfig,
                    Operator, mmin, mmax, initialize_function, MPI,
                    Abs, sqrt, sin, Constant, CustomDimension)

from devito.tools import as_tuple, memoized_func

try:
    from devitopro import *  # noqa
    from devitopro.subdomains import ABox
    AboxBase = ABox
except ImportError:
    ABox = None
    AboxBase = object


class ABoxSlowness(AboxBase):

    def _1d_cmax(self, vp, eps):
        cmaxs = []

        if eps is not None:
            assert vp.shape_allocated == eps.shape_allocated

        for (di, d) in enumerate(vp.grid.dimensions):
            rdim = tuple(i for (i, dl) in enumerate(vp.grid.dimensions) if dl is not d)

            # Max over other dimensions, 1D array with the max in each plane
            vpi = vp.data.min(axis=rdim)**(-.5)
            # THomsen correction
            if eps is not None:
                epsi = eps.data.max(axis=rdim)
                vpi._local[:] *= np.sqrt(1. + 2.*epsi._local[:])
            # Gather on all ranks if distributed.
            # Since we have a small-ish 1D vector we avoid the index gymnastic
            # and create the full 1d vector on al ranks with the local values
            # at the local indices and simply gather with Max
            if vp.grid.distributor.is_parallel:
                out = np.zeros(vp.grid.shape[di], dtype=vpi.dtype)
                tmp = np.zeros(vp.grid.shape[di], dtype=vpi.dtype)
                tmp[vp.local_indices[di]] = vpi._local
                vp.grid.distributor.comm.Allreduce(tmp, out, op=MPI.MAX)
                cmaxs.append(out)
            else:
                cmaxs.append(vpi)

        return cmaxs


__all__ = ['Model']


def getmin(f):
    if isinstance(f, Function):
        return mmin(f)
    elif isinstance(f, Constant):
        return f.data
    else:
        return np.min(f)


def getmax(f):
    if isinstance(f, Function):
        return mmax(f)
    elif isinstance(f, Constant):
        return f.data
    else:
        return np.max(f)


_thomsen = [('epsilon', 1), ('delta', 1), ('theta', 0), ('phi', 0)]


@memoized_func
def damp_op(ndim, padsizes, abc_type, fs):
    """
    Create damping field initialization operator.

    Parameters
    ----------
    ndim : int
        Number of dimensions in the model.
    padsizes : List of tuple
        Number of points in the damping layer for each dimension and side.
    abc_type : mask or damp
        whether the dampening is a mask or layer.
        mask => 1 inside the domain and decreases in the layer
        damp => 0 inside the domain and increase in the layer
    fs: bool
        Whether the model is with free surface or not
    """
    damp = Function(name="damp", grid=Grid(tuple([11]*ndim)), space_order=0)
    eqs = [Eq(damp, 1.0 if abc_type == "mask" else 0.0)]
    for (nbl, nbr), d in zip(padsizes, damp.dimensions):
        # 3 Point buffer to avoid weird interaction with abc
        if not fs or d is not damp.dimensions[-1]:
            nbl = nbl - 3
            dampcoeff = 1.5 * np.log(1.0 / 0.001) / (nbl)
            # left
            dim_l = SubDimension.left(name='abc_%s_l' % d.name, parent=d,
                                      thickness=nbl)
            pos = Abs((nbl - (dim_l - d.symbolic_min) + 1) / float(nbl))
            val = dampcoeff * (pos - sin(2*np.pi*pos)/(2*np.pi))
            val = -val if abc_type == "mask" else val
            eqs += [Inc(damp.subs({d: dim_l}), val/d.spacing)]
        # right
        nbr = nbr - 3
        dampcoeff = 1.5 * np.log(1.0 / 0.001) / (nbr)
        dim_r = SubDimension.right(name='abc_%s_r' % d.name, parent=d,
                                   thickness=nbr)
        pos = Abs((nbr - (d.symbolic_max - dim_r) + 1) / float(nbr))
        val = dampcoeff * (pos - sin(2*np.pi*pos)/(2*np.pi))
        val = -val if abc_type == "mask" else val
        eqs += [Inc(damp.subs({d: dim_r}), val/d.spacing)]

    return Operator(eqs, name='initdamp')


@switchconfig(log_level='ERROR')
def initialize_damp(damp, padsizes, abc_type="damp", fs=False):
    """
    Initialise damping field with an absorbing boundary layer.
    Includes basic constant Q setup (not interfaced yet) and assumes that
    the peak frequency is 1/(10 * spacing).

    Parameters
    ----------
    damp : Function
        The damping field for absorbing boundary condition.
    nbl : int
        Number of points in the damping layer.
    spacing :
        Grid spacing coefficient.
    mask : bool, optional
        whether the dampening is a mask or layer.
        mask => 1 inside the domain and decreases in the layer
        not mask => 0 inside the domain and increase in the layer
    """
    op = damp_op(damp.grid.dim, padsizes, abc_type, fs)
    op(damp=damp)


class Model(object):
    """
    The physical model used in seismic inversion
        shape_pml = np.array(shape) + 2 * self.nbl processes.

    Parameters
    ----------
    origin : tuple of floats
        Origin of the model in m as a tuple in (x,y,z) order.
    spacing : tuple of floats
        Grid size in m as a Tuple in (x,y,z) order.
    shape : tuple of int
        Number of grid points size in (x,y,z) order.
    space_order : int
        Order of the spatial stencil discretisation.
    m : array_like or float
        Squared slownes in s^2/km^2
    nbl : int, optional
        The number of absorbin layers for boundary damping.
    dtype : np.float32 or np.float64
        Defaults to 32.
    epsilon : array_like or float, optional
        Thomsen epsilon parameter (0<epsilon<1).
    delta : array_like or float
        Thomsen delta parameter (0<delta<1), delta<epsilon.
    theta : array_like or float
        Tilt angle in radian.
    phi : array_like or float
        Asymuth angle in radian.
    dt: Float
        User provided computational time-step
    abox: Float
        Whether to use the exapanding box, defaults to true
    """
    def __init__(self, origin, spacing, shape, space_order=8, nbl=40, dtype=np.float32,
                 m=None, epsilon=None, delta=None, theta=None, phi=None, rho=None,
                 b=None, qp=None, lam=None, mu=None, dm=None, fs=False, abox=True,
                 **kwargs):
        # Setup devito grid
        self.shape = tuple(shape)
        self.nbl = int(nbl)
        self.origin = tuple([dtype(o) for o in origin])
        abc_type = "mask" if (qp is not None or mu is not None) else "damp"
        self.fs = fs
        self._abox = abox
        # Origin of the computational domain with boundary to inject/interpolate
        # at the correct index
        origin_pml = [dtype(o - s*nbl) for o, s in zip(origin, spacing)]
        shape_pml = np.array(shape) + 2 * self.nbl
        if fs:
            origin_pml[-1] = origin[-1]
            shape_pml[-1] -= self.nbl
        # Physical extent is calculated per cell, so shape - 1
        extent = tuple(np.array(spacing) * (shape_pml - 1))
        self.grid = Grid(extent=extent, shape=shape_pml, origin=tuple(origin_pml),
                         dtype=dtype)

        # Absorbing boundary layer
        if self.nbl != 0:
            # Create dampening field as symbol `damp`
            self.damp = Function(name="damp", grid=self.grid, space_order=0)
            initialize_damp(self.damp, self.padsizes, abc_type=abc_type, fs=fs)
            self._physical_parameters = ['damp']
        else:
            self.damp = 1
            self._physical_parameters = []

        # Seismic fields and properties
        self.scale = 1
        self._space_order = space_order
        # Create square slowness of the wave as symbol `m`
        if m is not None:
            self._m = self._gen_phys_param(m, 'm', space_order)
        # density
        self._init_density(rho, b, space_order)
        # Perturbation for linearized modeling
        self._dm = self._gen_phys_param(dm, 'dm', space_order)

        # Model type
        self._is_viscoacoustic = qp is not None
        self._is_elastic = mu is not None
        self._is_tti = any(p is not None for p in [epsilon, delta, theta, phi])

        # Additional parameter fields for Viscoacoustic operators
        if self._is_viscoacoustic:
            self.qp = self._gen_phys_param(qp, 'qp', space_order)

        # Additional parameter fields for TTI operators
        if self._is_tti:
            epsilon = 1 if epsilon is None else 1 + 2 * epsilon
            delta = 1 if delta is None else 1 + 2 * delta
            self.epsilon = self._gen_phys_param(epsilon, 'epsilon', space_order)
            self.scale = np.sqrt(np.max(epsilon))
            self.delta = self._gen_phys_param(delta, 'delta', space_order)
            self.theta = self._gen_phys_param(theta, 'theta', space_order)
            if self.grid.dim == 3:
                self.phi = self._gen_phys_param(phi, 'phi', space_order)

        # Additional parameter fields for elastic
        if self._is_elastic:
            self.lam = self._gen_phys_param(lam, 'lam', space_order, is_param=True)
            self.mu = self._gen_phys_param(mu, 'mu', space_order, is_param=True)
        # User provided dt
        self._dt = kwargs.get('dt')

    def _init_density(self, rho, b, so):
        """
        Initialize density parameter. Depending on variance in density
        either density or inverse density is setup.
        """
        if rho is not None:
            rm, rM = np.amin(rho), np.amax(rho)
            if rm/rM > .1:
                self.irho = self._gen_phys_param(np.reciprocal(rho), 'irho', so)
                self.rho = 1 / self.irho
            else:
                self.rho = self._gen_phys_param(rho, 'rho', so)
                self.irho = 1 / self.rho
        elif b is not None:
            self.irho = self._gen_phys_param(b, 'irho', so)
        else:
            self.irho = 1

    @property
    def padsizes(self):
        padsizes = [(self.nbl, self.nbl) for _ in range(self.dim-1)]
        padsizes.append((0 if self.fs else self.nbl, self.nbl))
        return tuple(p for p in padsizes)

    def physical_params(self, **kwargs):
        """
        Return all set physical parameters and update to input values if provided
        """
        params = {i: kwargs.get(i, getattr(self, i)) for i in self._physical_parameters
                  if isinstance(getattr(self, i), Function) or
                  isinstance(getattr(self, i), Constant)}

        if not kwargs.get('born', False):
            params.pop('dm', None)

        return params

    @property
    def zero_thomsen(self):
        out = {}
        for (t, v) in _thomsen:
            try:
                out.update({getattr(self, t): v})
            except AttributeError:
                pass
        return out

    @switchconfig(log_level='ERROR')
    def _gen_phys_param(self, field, name, space_order, is_param=False,
                        default_value=0):
        """
        Create symbolic object an initiliaze its data
        """
        if field is None:
            return default_value
        if isinstance(field, np.ndarray):
            if field.shape == self.shape:
                function = Function(name=name, grid=self.grid, space_order=space_order,
                                    parameter=is_param)
                initialize_function(function, field, self.padsizes)
            else:
                # We take advantage of the external allocator
                function = Function(name=name, grid=self.grid, space_order=space_order,
                                    parameter=is_param)
                function.data[:] = field
        else:
            function = Constant(name=name, value=np.amin(field))
        self._physical_parameters.append(name)
        return function

    @property
    def physical_parameters(self):
        """
        List of physical parameteres
        """
        params = []
        for p in self._physical_parameters:
            if getattr(self, p).is_Constant:
                params.append('%s_const' % p)
            else:
                params.append(p)
        return as_tuple(params)

    @property
    def dim(self):
        """
        Spatial dimension of the problem and model domain.
        """
        return self.grid.dim

    @property
    def spacing(self):
        """
        Grid spacing for all fields in the physical model.
        """
        return self.grid.spacing

    @property
    def space_dimensions(self):
        """
        Spatial dimensions of the grid
        """
        return self.grid.dimensions

    @property
    def dtype(self):
        """
        Data type for all assocaited data objects.
        """
        return self.grid.dtype

    @property
    def domain_size(self):
        """
        Physical size of the domain as determined by shape and spacing
        """
        return tuple((d-1) * s for d, s in zip(self.shape, self.spacing))

    @property
    def space_order(self):
        """
        Spatial discretization order
        """
        return self._space_order

    @property
    def dt(self):
        """
        User provided dt
        """
        return self._dt

    @dt.setter
    def dt(self, dt):
        """
        Set user provided dt to overwrite the default CFL value.
        """
        self._dt = dt

    @property
    def is_tti(self):
        """
        Whether the model is TTI or isotopic
        """
        return self._is_tti

    @property
    def is_viscoacoustic(self):
        """
        Whether the model is TTI or isotopic
        """
        return self._is_viscoacoustic

    @property
    def is_elastic(self):
        """
        Whether the model is TTI or isotopic
        """
        return self._is_elastic

    @property
    def _max_vp(self):
        """
        Maximum velocity
        """
        if self.is_elastic:
            return np.sqrt(getmin(self.irho) * (getmax(self.lam) + 2 * getmax(self.mu)))
        else:
            return np.sqrt(1./getmin(self.m))

    @property
    def _cfl_coeff(self):
        """
        Courant number from the physics and spatial discretization order.
        The CFL coefficients are described in:
        - https://doi.org/10.1137/0916052 for the elastic case
        - https://library.seg.org/doi/pdf/10.1190/1.1444605 for the acoustic case
        """
        # Elasic coefficient (see e.g )
        if self.is_elastic:
            so = max(self.space_order // 2, 2)
            coeffs = fd_w(1, range(-so, so), .5)
            c_fd = sum(np.abs(coeffs[-1][-1])) / 2
            return .9 * np.sqrt(self.dim) / self.dim / c_fd
        a1 = 4  # 2nd order in time
        so = max(self.space_order // 2, 4)
        coeffs = fd_w(2, range(-so, so), 0)[-1][-1]
        return .9 * np.sqrt(a1/float(self.grid.dim * sum(np.abs(coeffs))))

    @property
    def _thomsen_scale(self):
        # Update scale for tti
        if self.is_tti:
            return np.sqrt(1 + 2 * getmax(self.epsilon))
        return 1

    @property
    def critical_dt(self):
        """
        Critical computational time step value from the CFL condition.
        """
        # For a fixed time order this number decreases as the space order increases.
        #
        # The CFL condtion is then given by
        # dt <= coeff * h / (max(velocity))
        dt = self._cfl_coeff * np.min(self.spacing) / (self._thomsen_scale*self._max_vp)
        dt = self.dtype("%.3e" % dt)
        if self.dt:
            if self.dt > dt:
                warnings.warn("Provided dt=%s is bigger than maximum stable dt %s "
                              % (self.dt, dt))
            else:
                return self.dtype("%.3e" % self.dt)
        return dt

    @property
    def dm(self):
        """
        Model perturbation for linearized modeling
        """
        return self._dm

    @dm.setter
    def dm(self, dm):
        """
        Set a new model perturbation.

        Parameters
        ----------
        dm : float or array
            New model perturbation
        """
        # Update the square slowness according to new value
        if isinstance(dm, np.ndarray):
            if not isinstance(self._dm, Function):
                self._dm = self._gen_phys_param(dm, 'dm', self.space_order)
            elif dm.shape == self.shape:
                initialize_function(self._dm, dm, self.padsizes)
            elif dm.shape == self.dm.shape:
                self.dm.data[:] = dm[:]
            else:
                raise ValueError("Incorrect input size %s for model of size" % dm.shape +
                                 " %s without or %s with padding" % (self.shape,
                                                                     self.dm.shape))
        else:
            try:
                self._dm.data = dm
            except AttributeError:
                self._dm = dm

    @property
    def m(self):
        """
        Function holding the squared slowness in s^2/km^2.
        """
        return self._m

    @m.setter
    def m(self, m):
        """
        Set a new squared slowness model.

        Parameters
        ----------
        m : float or array
            New squared slowness in s^2/km^2.
        """
        # Update the square slowness according to new value
        if isinstance(m, np.ndarray):
            if m.shape == self.m.shape:
                self.m.data[:] = m[:]
            elif m.shape == self.shape:
                initialize_function(self._m, m, self.padsizes)
            else:
                raise ValueError("Incorrect input size %s for model of size" % m.shape +
                                 " %s without or %s with padding" % (self.shape,
                                                                     self.m.shape))
        else:
            self._m.data = m

    @property
    def vp(self):
        """
        Symbolic representation of the velocity
        vp = sqrt(1 / m)
        """
        return sqrt(1 / self.m)

    @property
    def spacing_map(self):
        """
        Map between spacing symbols and their values for each `SpaceDimension`.
        """
        sp_map = self.grid.spacing_map
        return sp_map

    def abox(self, src, rec, fw=True):
        if ABox is None:
            return {}
        if not fw:
            src, rec = rec, src
        eps = getattr(self, 'epsilon', None)
        abox = ABoxSlowness(src, rec, self.m, self.space_order, eps=eps)
        return {'abox': abox}

    def __init_abox__(self, src, rec, fw=True):
        return

    @cached_property
    def physical(self):
        if ABox is None:
            return None
        else:
            return self._abox

    @cached_property
    def fs_dim(self):
        so = self.space_order // 2
        return CustomDimension(name="zfs", symbolic_min=1,
                               symbolic_max=so,
                               symbolic_size=so)


class EmptyModel():
    """
    An pseudo Model structure that does not contain any physical field
    but only the necessary information to create an operator.
    This Model should not be used for propagation.
    """

    def __init__(self, tti, visco, elastic, spacing, fs, space_order, p_params):
        self.is_tti = tti
        self.is_viscoacoustic = visco
        self.is_elastic = elastic
        self.spacing = spacing
        self.fs = fs
        self.space_order = space_order
        N = 2 * space_order + 1

        self.grid = Grid(tuple([N]*len(spacing)),
                         extent=[s*(N-1) for s in spacing])
        self.dimensions = self.grid.dimensions

        # Create the function for the physical parameters
        self.damp = Function(name='damp', grid=self.grid, space_order=0)
        for p in set(p_params) - {'damp'}:
            if p.endswith('_const'):
                name = p.split('_')[0]
                setattr(self, name, Constant(name=name, value=1))
            else:
                setattr(self, p, Function(name=p, grid=self.grid,
                                          space_order=space_order))
        if 'irho' not in p_params and 'irho_const' not in p_params:
            self.irho = 1 if 'rho' not in p_params else 1 / self.rho

    @property
    def spacing_map(self):
        """
        Map between spacing symbols and their values for each `SpaceDimension`.
        """
        return self.grid.spacing_map

    @property
    def critical_dt(self):
        """
        User provided dt
        """
        return self.grid.time_dim.spacing

    @property
    def dim(self):
        """
        Spatial dimension of the problem and model domain.
        """
        return self.grid.dim

    @property
    def zero_thomsen(self):
        out = {}
        for (t, v) in _thomsen:
            try:
                out.update({getattr(self, t): v})
            except AttributeError:
                pass
        return out

    def __init_abox__(self, src, rec, fw=True):
        if ABox is None:
            return
        eps = getattr(self, 'epsilon', None)
        if not fw:
            src, rec = rec, src
        self._abox = ABoxSlowness(src, rec, self.m, self.space_order, eps=eps)

    @cached_property
    def physical(self):
        if ABox is None:
            return None
        else:
            return self._abox

    @cached_property
    def fs_dim(self):
        so = self.space_order // 2
        return CustomDimension(name="zfs", symbolic_min=1,
                               symbolic_max=so,
                               symbolic_size=so)
