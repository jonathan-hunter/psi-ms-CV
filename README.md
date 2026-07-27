## HUPO-PSI mass spectrometry controlled vocabulary (psi-ms)
[![release](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/make-release-on-tag.yml/badge.svg)](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/make-release-on-tag.yml)
[![OBO validation](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/validate-obo.yml/badge.svg)](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/validate-obo.yml)
[![Update OWL](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/update-owl.yaml/badge.svg)](https://github.com/HUPO-PSI/psi-ms-CV/actions/workflows/update-owl.yaml)

The [Human Proteome Organization (HUPO)–Proteomics Standards Initiative (PSI)](https://psidev.info/) extensively uses ontologies and controlled vocabularies (CVs) in their data formats. The PSI-Mass Spectrometry controlled vocabulary (PSI-MS) is the main ontology from PSI that store and control all terms for MS-based proteomics experiments. It encompasses terms for a complete MS analysis pipeline, including sample labeling, digestion enzymes, instrumentation, software for peptide/protein identification and quantification, and parameters for significance determination. This CV's development involved collaboration across PSI working groups, proteomics researchers, instrument manufacturers, and software vendors. This article outlines the CV's structure, development, maintenance, and dependencies on other ontologies.

### OBO and OWL files

The ontology is maintained as two OBO source components and released in OBO and OWL:

- **psi-ms-core.obo**: manually maintained PSI-MS terms.
- **psi-ms-columns.obo**: chromatographic-column terms generated from repo-rt.
- **psi-ms.obo** and **psi-ms.owl**: read-only release files generated from both components.
- **VERSION**: the version assigned to the merged release files. Components are versioned by Git and do not carry `data-version` headers.

ROBOT performs the merge, assigns the release ontology/version IRIs, writes `psi-ms.owl`, and converts that same merged ontology to `psi-ms.obo`.

```sh
make ROBOT="java -jar /path/to/robot.jar" release verify
```

Do not edit either release file directly.

### Requesting a new term

Anyone can request a new term be added to the controlled vocabulary by opening an issue or a pull
request against this repository. We'd appreciate any help you can contribute when submitting a new
term, from proposing the term name and description to defining its relationships and properties. 

### Submitting a new term 

To submit a new term, fork this repository or create a branch, add the term to **psi-ms-core.obo**, and increment **VERSION**. Do not modify `psi-ms.obo` or `psi-ms.owl`; the release workflow regenerates both. Then open a pull request for review by a HUPO-PSI CV maintainer. Alternatively, open an issue with the available information and we will help prepare the term.

> If you're requesting multiple related terms, you can submit them in a single issue/pull request.

When either source component changes, increment `VERSION` and add your name to the contributor remarks in `psi-ms-core.obo`. Release metadata is added by ROBOT, so component headers do not need `date` or `data-version` edits.

```text
4.1.258
```

### How to cite

When you use psi-ms.obo, please cite the following publication:

>Mayer G, Montecchi-Palazzi L, Ovelleiro D, Jones AR, Binz PA, Deutsch EW, Chambers M, Kallhardt M, Levander F, Shofstahl J, Orchard S, Vizcaíno JA, Hermjakob H, Stephan C, Meyer HE, Eisenacher M; HUPO-PSI Group. The HUPO proteomics standards initiative- mass spectrometry controlled vocabulary. Database (Oxford). 2013 Mar 12;2013:bat009. doi: 10.1093/database/bat009. Print 2013.  [pdf](http://database.oxfordjournals.org/content/2013/bat009.full.pdf+html)
