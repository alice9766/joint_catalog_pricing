# Implementation and packaging notes

The repository combines the experiment sources used by the current paper. It preserves the numerical observations rather than replacing them with fresh results obtained while preparing this package.

The public entry points use repository-relative paths and refuse to overwrite an existing result directory. Each experiment retains the solver source used for its observations. The experiment documents identify any wrapper or portability changes. These changes do not alter the optimization model, candidate sets, objective, or tie convention.

The original figure source is preserved in `figures/draw_section5_english.py`. The reproduction wrapper imports its drawing functions and redirects the output directory. It supplies figure data independently reconstructed from the experiment records, checks those data against the frozen paper snapshot, and uses the supplied font. Historical source paths in original observation metadata describe provenance and are not required for execution.

The 300-second joint-scan batch is the source of the current Table II. The older 30-second joint-scan release is not used. Table II retains its current six configurations with B=K=4; the two extra B=K=6 inputs from the same 300-second batch remain available with their six observations.

The structure-suite and S1 baseline values include the existing fixed-catalog repricing corrections. The original observations and correction linkage are preserved, and the reconstruction applies the correction mapping before calculating Figure 6. The source and mapping are described in `STRUCTURE.md`.

The comparison retains both the MILP's weak-IC assignment objective and the returned menu's canonical profit. A time-limited incumbent objective is not an upper bound. The upper bound is obtained from the solver's reported dual bound with the appropriate sign; see `MILP.md`.

The main manuscript, experimental observations, and complete proofs were not revised during this packaging step. A fresh solve is recorded separately and must not be substituted for a stored observation when reconstructing the current paper.
