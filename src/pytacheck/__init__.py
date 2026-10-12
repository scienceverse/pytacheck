"""pytacheck is the old import name of metacheck, kept so that existing code keeps working.

``import pytacheck.<sub>`` gives the ``metacheck.<sub>`` module itself, not a copy,
and the names of ``import pytacheck`` come from ``metacheck``. New code should
``import metacheck``.
"""

from metacheck._alias import install as _install

_install(__name__, "metacheck")
del _install
