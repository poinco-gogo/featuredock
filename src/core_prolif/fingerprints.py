"""
Calculate a Protein-Ligand Interaction Fingerprint --- :mod:`prolif.fingerprint`
================================================================================

.. ipython:: python
    :okwarning:

    import MDAnalysis as mda
    from rdkit.DataStructs import TanimotoSimilarity
    import prolif
    u = mda.Universe(prolif.datafiles.TOP, prolif.datafiles.TRAJ)
    prot = u.select_atoms("protein")
    lig = u.select_atoms("resname LIG")
    fp = prolif.Fingerprint(["HBDonor", "HBAcceptor", "PiStacking", "CationPi",
                             "Cationic"])
    fp.run(u.trajectory[::10], lig, prot)
    df = fp.to_dataframe()
    df
    bv = fp.to_bitvectors()
    TanimotoSimilarity(bv[0], bv[1])
    # save results
    fp.to_pickle("fingerprint.pkl")
    # load
    fp = prolif.Fingerprint.from_pickle("fingerprint.pkl")
    df.equals(fp.to_dataframe())

"""

import pickle
import numpy as np
from .interactions import _INTERACTIONS
from .molecules import get_residues_near_ligand


class _Docstring:
    """Descriptor that replaces the documentation shown when calling
    ``fp.hydrophobic?`` and other interaction methods"""
    def __init__(self):
        self._docs = {}

    def __set__(self, instance, func):
        # add function's docstring to memory
        cls = func.__self__.__class__
        self._docs[cls.__name__] = cls.__doc__

    def __get__(self, instance, owner):
        if instance is None:
            return self
        # fetch docstring of last accessed Fingerprint method
        return self._docs[type(instance)._current_func]


class _InteractionWrapper:
    """Modifies the return signature of an interaction ``detect`` method by
    forcing it to return only the first element when multiple values are
    returned.

    Raises
    ------
    TypeError
        If the ``detect`` method doesn't return three values

    Notes
    -----
    The original return signature of the decorated function is still accessible
    by calling ``function.__wrapped__(*args, **kwargs)``.

    Example
    -------
    ::

        >>> class Foo:
        ...     def detect(self, *args):
        ...         return 1, 2, 3
        ...
        >>> foo = Foo().detect
        >>> bar = _InteractionWrapper(foo)
        >>> foo()
        (1, 2, 3)
        >>> bar()
        1
        >>> bar.__wrapped__()
        (1, 2, 3)

    .. versionchanged:: 0.3.3
        The function now must return three values

    .. versionchanged:: 1.0.0
        Changed from a wrapper function to a class for easier pickling support

    """
    __doc__ = _Docstring()
    _current_func = ""

    def __init__(self, func):
        self.__wrapped__ = func
        # add docstring to descriptor
        self.__doc__ = func

    def __repr__(self):  # pragma: no cover
        cls = self.__wrapped__.__self__.__class__
        return f"<{cls.__module__}.{cls.__name__} at {id(self):#x}>"

    def __call__(self, *args, **kwargs):
        results = self.__wrapped__(*args, **kwargs)
        try:
            bool_, lig_idx, prot_idx = results
        except (TypeError, ValueError):
            raise TypeError(
                "Incorrect function signature: the interaction class must "
                "return 3 values (boolean, int, int)"
            ) from None
        return bool_


class Fingerprint:
    """Class that generates an interaction fingerprint between two molecules

    While in most cases the fingerprint will be generated between a ligand and
    a protein, it is also possible to use this class for protein-protein
    interactions, or host-guest systems.

    Parameters
    ----------
    interactions : list
        List of names (str) of interaction classes as found in the
        :mod:`prolif.interactions` module

    Attributes
    ----------
    interactions : dict
        Dictionnary of interaction functions indexed by class name. For more
        details, see :mod:`prolif.interactions`
    n_interactions : int
        Number of interaction functions registered by the fingerprint
    ifp : list, optionnal
        List of interactions fingerprints for the given trajectory.

    Raises
    ------
    NameError : Unknown interaction in the ``interactions`` parameter

    Notes
    -----
    You can use the fingerprint generator in multiple ways:

    - On a trajectory directly from MDAnalysis objects:

    .. ipython:: python

        prot = u.select_atoms("protein")
        lig = u.select_atoms("resname LIG")
        fp = prolif.Fingerprint(["HBDonor", "HBAcceptor", "PiStacking",
                                 "Hydrophobic"])
        fp.run(u.trajectory[:5], lig, prot)
        fp.to_dataframe()

    - On two single structures (from RDKit or MDAnalysis):

    .. ipython:: python

        u.trajectory[0]  # use coordinates of the first frame
        prot = prolif.Molecule.from_mda(prot)
        lig = prolif.Molecule.from_mda(lig)
        ifp = fp.generate(lig, prot)
        ifp["Frame"] = 0
        prolif.to_dataframe([ifp], fp.interactions.keys())

    - On a specific pair of residues for a specific interaction:

    .. ipython:: python

        # ligand-protein
        fp.hbdonor(lig, prot["ASP129.A"])
        # protein-protein (alpha helix)
        fp.hbacceptor(prot["ASP129.A"], prot["CYS133.A"])

    You can also obtain the indices of atoms responsible for the interaction:

    .. ipython:: python

        fp.bitvector_atoms(lig, prot["ASP129.A"])
        fp.hbdonor.__wrapped__(lig, prot["ASP129.A"])


    .. versionchanged:: 1.0.0
        Added pickle support

    """

    def __init__(self, interactions=["Hydrophobic", "HBDonor", "HBAcceptor",
                 "PiStacking", "Anionic", "Cationic", "CationPi", "PiCation"]):
        self._set_interactions(interactions)

    def _set_interactions(self, interactions):
        # read interactions to compute
        self.interactions = {}
        if interactions == "all":
            interactions = self.list_available()
        # sanity check
        unsafe = set(interactions)
        unk = unsafe.symmetric_difference(_INTERACTIONS.keys()) & unsafe
        if unk:
            raise NameError(f"Unknown interaction(s): {', '.join(unk)}")
        # add interaction methods
        for name, interaction_cls in _INTERACTIONS.items():
            if name.startswith("_") or name == "Interaction":
                continue
            func = interaction_cls().detect
            func = _InteractionWrapper(func)
            setattr(self, name.lower(), func)
            if name in interactions:
                self.interactions[name] = func

    def __getattribute__(self, name):
        # trick to get the correct docstring when calling `fp.hydrophobic?`
        attr = super().__getattribute__(name)
        if isinstance(attr, _InteractionWrapper):
            type(attr)._current_func = attr.__wrapped__.__self__.__class__.__name__
        return attr

    def __repr__(self):  # pragma: no cover
        name = ".".join([self.__class__.__module__, self.__class__.__name__])
        params = f"{self.n_interactions} interactions: {list(self.interactions.keys())}"
        return f"<{name}: {params} at {id(self):#x}>"

    @staticmethod
    def list_available(show_hidden=False):
        """List interactions available to the Fingerprint class.

        Parameters
        ----------
        show_hidden : bool
            Show hidden classes (usually base classes whose name starts with an
            underscore ``_``. Those are not supposed to be called directly)
        """
        if show_hidden:
            interactions = [name for name in _INTERACTIONS.keys()]
        else:
            interactions = [name for name in _INTERACTIONS.keys()
                            if not (name.startswith("_")
                                    or name == "Interaction")]
        return sorted(interactions)

    @property
    def n_interactions(self):
        return len(self.interactions)


    def bitvector_atoms(self, res1, res2):
        """Generates the complete bitvector for the interactions between two
        residues, and returns the indices of atoms responsible for these
        interactions

        Parameters
        ----------
        res1 : prolif.residue.Residue or prolif.molecule.Molecule
            A residue, usually from a ligand
        res2 : prolif.residue.Residue or prolif.molecule.Molecule
            A residue, usually from a protein

        Returns
        -------
        bitvector : numpy.ndarray
            An array storing the encoded interactions between res1 and res2
        lig_atoms : list
            A list containing indices for the ligand atoms responsible for each
            interaction
        pro_atoms : list
            A list containing indices for the protein atoms responsible for
            each interaction


        .. versionchanged:: 0.3.2
            Atom indices are returned as two separate lists instead of a single
            list of tuples

        """
        bitvector = []
        lig_atoms = []
        prot_atoms = []
        for func in self.interactions.values():
            bit, la, pa = func.__wrapped__(res1, res2)
            bitvector.append(bit)
            lig_atoms.append(la)
            prot_atoms.append(pa)
        bitvector = np.array(bitvector)
        return bitvector, lig_atoms, prot_atoms

    def generate(self, lig, prot, residues=None):
        """Generates the interaction fingerprint between 2 molecules

        Parameters
        ----------
        lig : prolif.molecule.Molecule
            Molecule for the ligand
        prot : prolif.molecule.Molecule
            Molecule for the protein
        residues : list or "all" or None
            A list of protein residues (:class:`str`, :class:`int` or
            :class:`~prolif.residue.ResidueId`) to take into account for
            the fingerprint extraction. If ``"all"``, all residues will be
            used. If ``None``, at each frame the
            :func:`~prolif.utils.get_residues_near_ligand` function is used to
            automatically use protein residues that are distant of 6.0 Å or
            less from each ligand residue.
        return_atoms : bool
            For each residue pair and interaction, return indices of atoms
            responsible for the interaction instead of bits.

        Returns
        -------
        ifp : dict
            A dictionnary indexed by ``(ligand, protein)`` residue pairs. The
            format for values will depend on ``return_atoms``:

            - A single bitvector if ``return_atoms=False``
            - A tuple of bitvector, ligand atom indices and protein atom
              indices if ``return_atoms=True``

        Example
        -------
        ::

            >>> u = mda.Universe("complex.pdb")
            >>> lig = prolif.Molecule.from_mda(u, "resname LIG")
            >>> prot = prolif.Molecule.from_mda(u, "protein")
            >>> fp = prolif.Fingerprint()
            >>> ifp = fp.generate(lig, prot)

        .. versionadded:: 0.3.2
        """
        ifp = {}
        resids = residues
        if residues == "all":
            resids = prot.residues.keys()
        for lresid, lres in lig.residues.items():
            if residues is None:
                resids = get_residues_near_ligand(lres, prot)
            for prot_key in resids:
                pres = prot[prot_key]
                key = (lresid, pres.resid)
                ifp[key] = self.bitvector_atoms(lres, pres)
        return ifp


    def to_pickle(self, path=None):
        """Dumps the fingerprint object as a pickle.

        Parameters
        ----------
        path : str, pathlib.Path or None
            Output path. If ``None``, the method returns the pickle as bytes.

        Returns
        -------
        obj : None or bytes
            ``None`` if ``path`` is set, else the bytes corresponding to the
            pickle

        Example
        -------
        ::

            >>> dump = fp.to_pickle()
            >>> saved_fp = Fingerprint.from_pickle(dump)
            >>> fp.to_pickle("data/fp.pkl")
            >>> saved_fp = Fingerprint.from_pickle("data/fp.pkl")

        .. seealso::

            :meth:`~Fingerprint.from_pickle` for loading the pickle dump.

        .. versionadded:: 1.0.0

        """
        if path:
            with open(path, "wb") as f:
                pickle.dump(self, f)
        else:
            return pickle.dumps(self)

    @classmethod
    def from_pickle(cls, path_or_bytes):
        """Creates a fingerprint object from a pickle dump.

        Parameters
        ----------
        path_or_bytes : str, pathlib.Path or bytes
            The path to the pickle file, or bytes corresponding to a pickle
            dump

        .. seealso::

            :meth:`~Fingerprint.to_pickle` for creating the pickle dump.

        .. versionadded:: 1.0.0

        """
        if isinstance(path_or_bytes, bytes):
            return pickle.loads(path_or_bytes)
        with open(path_or_bytes, "rb") as f:
            return pickle.load(f)
