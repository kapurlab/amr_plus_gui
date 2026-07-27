/* SHARED COMPONENT — byte-identical across the Kapur Lab tool suite.
   Source of truth: amr_plus_gui/frontend/src/Citations.jsx
   Do not edit in one repo. Change it here, then re-copy to every sibling and
   re-tag. Verify with bin/check-shared-frontend.sh in the umbrella.

   Every GUI carries the same footer: the citation for this suite, plus the
   citation(s) for the upstream analysis tool that produced the results on
   screen. Diagnostic output gets used in publications and reports, and the
   person who needs the reference is looking at the result, not at a README —
   so the reference lives at the bottom of the page that made the result.

   The whole registry ships in every copy so the file stays byte-identical; a
   tool selects its entry with the `tool` prop. Keys are the tool repo names.

   Every reference below was checked against Europe PMC / the upstream repo.
   Do not add one from memory — a wrong volume or DOI in a footer propagates
   into other people's bibliographies. */
import React, { useState } from "react";
import "./Citations.css";

/* The suite itself. Shown by every tool, above the upstream citation. */
const SUITE = {
  label: "This tool suite",
  text:
    "Kapur Laboratory bioinformatic diagnostic tools (bdtools). " +
    "Pennsylvania State University. " +
    "https://github.com/kapurlab/bioinformatic_diagnostic_tools",
  href: "https://github.com/kapurlab/bioinformatic_diagnostic_tools",
};

/* Upstream analysis tools, keyed by tool repo name. `text` is what a user
   copies into a manuscript; `href` resolves to the DOI or the source repo. */
const UPSTREAM = {
  vsnp_gui: [
    {
      label: "vSNP3",
      text:
        "Hicks J, Stuber T, Lantz K, Torchetti M, Robbe-Austerman S. " +
        "vSNP: a SNP pipeline for the generation of transparent SNP matrices " +
        "and phylogenetic trees from whole genome sequencing data sets. " +
        "BMC Genomics. 2024;25(1):545. doi:10.1186/s12864-024-10437-5",
      href: "https://doi.org/10.1186/s12864-024-10437-5",
    },
  ],
  amr_plus_gui: [
    {
      label: "AMRFinderPlus",
      text:
        "Feldgarden M, Brover V, Gonzalez-Escalona N, Frye JG, Haendiges J, " +
        "Haft DH, Hoffmann M, Pettengill JB, Prasad AB, Tillman GE, Tyson GH, " +
        "Klimke W. AMRFinderPlus and the Reference Gene Catalog facilitate " +
        "examination of the genomic links among antimicrobial resistance, " +
        "stress response, and virulence. Sci Rep. 2021;11:12728. " +
        "doi:10.1038/s41598-021-91456-0",
      href: "https://doi.org/10.1038/s41598-021-91456-0",
    },
    /* This GUI also runs mlst to resolve the organism token AMRFinderPlus is
       given, so the typing scheme source belongs in the same footer. */
    {
      label: "MLST (organism resolution)",
      text:
        "Seemann T. mlst. https://github.com/tseemann/mlst — using the PubMLST " +
        "website (https://pubmlst.org/) developed by Jolley KA, Bray JE, " +
        "Maiden MCJ. Open-access bacterial population genomics: BIGSdb " +
        "software, the PubMLST.org website and their applications. " +
        "Wellcome Open Res. 2018;3:124. doi:10.12688/wellcomeopenres.14826.1",
      href: "https://doi.org/10.12688/wellcomeopenres.14826.1",
    },
  ],
  irma_gui: [
    {
      label: "IRMA",
      text:
        "Shepard SS, Meno S, Bahl J, Wilson MM, Barnes J, Neuhaus E. " +
        "Viral deep sequencing needs an adaptive approach: IRMA, the iterative " +
        "refinement meta-assembler. BMC Genomics. 2016;17:708. " +
        "doi:10.1186/s12864-016-3030-6",
      href: "https://doi.org/10.1186/s12864-016-3030-6",
    },
  ],
  genoflu_gui: [
    /* GenoFLU has no dedicated software paper. Its README asks users to cite
       the Youk et al. genotype paper, so that is what this shows — with the
       tool repo alongside it, since the reference alone doesn't name the tool. */
    {
      label: "GenoFLU",
      text:
        "Youk S, Torchetti MK, Lantz K, Lenoch JB, Killian ML, Leyson C, " +
        "Bevins SN, Dilione K, Ip HS, Stallknecht DE, Poulson RL, Suarez DL, " +
        "Swayne DE, Pantin-Jackwood MJ. H5N1 highly pathogenic avian influenza " +
        "clade 2.3.4.4b in wild and domestic birds: Introductions into the " +
        "United States and reassortments, December 2021-April 2022. " +
        "Virology. 2023;587:109860. doi:10.1016/j.virol.2023.109860",
      href: "https://doi.org/10.1016/j.virol.2023.109860",
    },
    {
      label: "GenoFLU (software)",
      text: "USDA-VS GenoFLU. https://github.com/USDA-VS/GenoFLU",
      href: "https://github.com/USDA-VS/GenoFLU",
    },
  ],
  mlst_gui: [
    {
      label: "mlst",
      text: "Seemann T. mlst. https://github.com/tseemann/mlst",
      href: "https://github.com/tseemann/mlst",
    },
    {
      label: "PubMLST / BIGSdb",
      text:
        "Jolley KA, Bray JE, Maiden MCJ. Open-access bacterial population " +
        "genomics: BIGSdb software, the PubMLST.org website and their " +
        "applications. Wellcome Open Res. 2018;3:124. " +
        "doi:10.12688/wellcomeopenres.14826.1",
      href: "https://doi.org/10.12688/wellcomeopenres.14826.1",
    },
  ],
  kraken_id_parse_gui: [
    {
      label: "Kraken 2",
      text:
        "Wood DE, Lu J, Langmead B. Improved metagenomic analysis with " +
        "Kraken 2. Genome Biol. 2019;20:257. doi:10.1186/s13059-019-1891-0",
      href: "https://doi.org/10.1186/s13059-019-1891-0",
    },
    {
      label: "Krona",
      text:
        "Ondov BD, Bergman NH, Phillippy AM. Interactive metagenomic " +
        "visualization in a Web browser. BMC Bioinformatics. 2011;12:385. " +
        "doi:10.1186/1471-2105-12-385",
      href: "https://doi.org/10.1186/1471-2105-12-385",
    },
    /* Bracken drives the abundance / pie-chart step, which is part of the
       pipeline everywhere it can run — it has no osx-64 build for python 3.10,
       so that one step is skipped on Apple Silicon. Listed unconditionally: the
       backend exposes no "did Bracken run" signal, and a citation that appears
       and disappears per host is worse than one extra reference. A caller with a
       better signal can drop it with omit={["Bracken"]}. */
    {
      label: "Bracken",
      text:
        "Lu J, Breitwieser FP, Thielen P, Salzberg SL. Bracken: estimating " +
        "species abundance in metagenomics data. PeerJ Comput Sci. 2017;3:e104. " +
        "doi:10.7717/peerj-cs.104",
      href: "https://doi.org/10.7717/peerj-cs.104",
    },
  ],
  ksnp_gui: [
    {
      label: "kSNP4",
      text:
        "Hall BG, Nisbet J. Building Phylogenetic Trees From Genome Sequences " +
        "With kSNP4. Mol Biol Evol. 2023;40(11):msad235. " +
        "doi:10.1093/molbev/msad235",
      href: "https://doi.org/10.1093/molbev/msad235",
    },
  ],
  /* No upstream analysis tool of its own — it prepares NCBI submissions. */
  ncbi_submit_gui: [],
  /* Developmental. Its typing calls are not validated for diagnostic use, so it
     deliberately advertises no method citation of its own yet. */
  mhc_gui: [],
};

function Reference({ entry }) {
  return (
    <li className="cite-item">
      <span className="cite-label">{entry.label}</span>
      <span className="cite-text">{entry.text}</span>
      {entry.href && (
        <a
          className="cite-link"
          href={entry.href}
          target="_blank"
          rel="noreferrer noopener"
        >
          open ↗
        </a>
      )}
    </li>
  );
}

/**
 * Citation footer.
 *
 * @param tool  tool repo name, e.g. "ksnp_gui" — selects the upstream entries
 * @param omit  labels to leave out (e.g. ["Bracken"] where that step can't run)
 */
export default function CitationFooter({ tool, omit = [] }) {
  const [copied, setCopied] = useState(false);
  const upstream = (UPSTREAM[tool] || []).filter((e) => !omit.includes(e.label));
  const all = [SUITE, ...upstream];

  const copyAll = async () => {
    const text = all.map((e) => e.text).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* Clipboard is blocked without a secure context or user permission. The
         references are on screen and selectable, so this is not worth an error
         dialog — just don't claim it was copied. */
      setCopied(false);
    }
  };

  return (
    <footer className="cite-footer">
      <div className="cite-head">
        <h2 className="cite-title">How to cite</h2>
        <button type="button" className="cite-copy ghost" onClick={copyAll}>
          {copied ? "Copied" : "Copy all"}
        </button>
      </div>
      <p className="cite-intro">
        If results from this tool appear in a publication or report, please cite
        this suite and the analysis tool that produced them.
      </p>
      <ul className="cite-list">
        {all.map((e) => (
          <Reference key={e.label} entry={e} />
        ))}
      </ul>
    </footer>
  );
}
