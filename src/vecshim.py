"""Minimal pure-Python stand-in for the tiny slice of PyGLM this project actually uses:
fixed-size numeric structs with .x/.y/.z access, indexing, and equality. Never used for
real 3D math (no matrices, no dot/cross products) - just x/y/z containers for board
coordinates and RGB colors - so this shim, unlike PyGLM, has no native/compiled
dependency and works anywhere plain Python does (including under Chaquopy on Android)."""


class _Vec:
    _size = 0
    _cast = int

    def __init__(self, *args):
        cast = self._cast
        if len(args) == 1:
            arg = args[0]
            if isinstance(arg, (int, float)):
                self._v = [cast(arg)] * self._size
            else:
                self._v = [cast(c) for c in arg]
        else:
            self._v = [cast(a) for a in args]
        if len(self._v) != self._size:
            raise ValueError(f"expected {self._size} components, got {len(self._v)}")

    @property
    def x(self):
        return self._v[0]

    @x.setter
    def x(self, val):
        self._v[0] = self._cast(val)

    @property
    def y(self):
        return self._v[1]

    @y.setter
    def y(self, val):
        self._v[1] = self._cast(val)

    def __len__(self):
        return self._size

    def __iter__(self):
        return iter(self._v)

    def __getitem__(self, i):
        return self._v[i]

    def __setitem__(self, i, val):
        self._v[i] = self._cast(val)

    def __eq__(self, other):
        try:
            return list(self._v) == list(other)
        except TypeError:
            return NotImplemented

    def __repr__(self):
        return f"{type(self).__name__}({', '.join(map(str, self._v))})"

    def __copy__(self):
        new = type(self).__new__(type(self))
        new._v = list(self._v)
        return new


class _Vec3Mixin:
    _size = 3

    @property
    def z(self):
        return self._v[2]

    @z.setter
    def z(self, val):
        self._v[2] = self._cast(val)


class ivec2(_Vec):
    _size = 2
    _cast = int


class vec2(_Vec):
    _size = 2
    _cast = float


class ivec3(_Vec3Mixin, _Vec):
    _cast = int


class vec3(_Vec3Mixin, _Vec):
    _cast = float
