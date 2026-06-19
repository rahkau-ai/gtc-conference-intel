#!/usr/bin/env python3
"""
Stage 3 — Python fact extraction (zero Claude tokens).

Processes ASGCT 2026 abstract chunk .txt files → wide CSV + JSONL.
Resume-safe: appends to existing CSV, skips already-extracted abstract_ids.

Schema (33 columns):
  Tier 1 (16 cols):     always filled or UNKNOWN
  Tier 2 base (9 cols): filled if present, "not_reported" if absent
  Tier 2 intel (7 cols): intelligence signals — development_stage, sponsor_type,
                          ip_signals, aav_capsid, therapeutic_payload, trial_id,
                          manufacturing_gmp_signal ("UNKNOWN" when absent)
  Reserved (1 col):     scientific_comment (blank placeholder)

Usage:
  # Dry-run on single chunk — prints 3 examples, no file write
  python extract_python.py --chunk <path.txt> --config <config.json> --dry-run

  # Full run — all chunks in directory, resume-safe
  python extract_python.py --all-chunks <dir> --config <config.json> \
    --out-csv <output.csv> --out-jsonl <output.jsonl>
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows (avoids cp1252 crash on special chars)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ─── SCHEMA ──────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    # Tier 1 (required — always filled or UNKNOWN)
    "row_id", "abstract_id", "title", "chunk_pages",
    "first_author", "last_author", "organisation", "geography",
    "modality", "disease",
    "what_found", "evidence_quote", "confidence",
    "schema_version", "citation", "source_id",
    # Tier 2 (extract-if-present — "not_reported" when absent)
    "animal_models", "cell_lines", "dose_raw", "route_of_admin",
    "time_points", "methods", "assays", "instruments",
    "all_authors_raw",
    # Tier 2 — intelligence signals (v1-python upgrade, default "UNKNOWN")
    "development_stage", "sponsor_type", "ip_signals", "aav_capsid",
    "therapeutic_payload", "trial_id", "manufacturing_gmp_signal",
    # Reserved — blank placeholder for future scientific reviewer pass
    "scientific_comment",
]

# ─── VALID ASGCT 2026 ABSTRACT ID RANGES ────────────────────────────────────
# Sparse numbering: 6 sections with gaps
VALID_RANGES = [(1, 537), (600, 626), (1000, 1519), (2000, 2519), (3001, 3521), (4000, 4019)]

def is_valid_asgct_id(id_str):
    try:
        n = int(id_str)
        return any(lo <= n <= hi for lo, hi in VALID_RANGES)
    except ValueError:
        return False

# ─── HEADING DETECTION ───────────────────────────────────────────────────────
# Abstract headings: 1-4 digit ID followed by a title (min 10 chars, starts with capital)
# Only match when preceded by blank lines (to avoid false matches mid-abstract)
HEADING_RE = re.compile(
    r'(?:(?:^|\n)[ \t]*\n){1,}[ \t]*(?P<id>\d{1,4})[ \t]+(?P<title>[A-Z][^\n]{9,})',
    re.MULTILINE
)

# ─── SECTION HEADERS ─────────────────────────────────────────────────────────
SECTION_RE = re.compile(
    r'\n[ \t]*(Introduction|Methods?|Results?|Conclusions?)\b',
    re.IGNORECASE
)

# ─── MODALITY PATTERNS (gene_therapy / gene_editing / cell_therapy / mRNA) ───
MODALITY_PATTERNS = [
    ("gene_therapy",  r'\b(?:AAV|adeno.?associated\s+virus|lentivir|retrovir|adenovir)\b'),
    ("gene_editing",  r'\b(?:CRISPR|Cas9|Cas12|base[\s\-]?edit(?:ing|or)?|prime[\s\-]?edit(?:ing|or)?|zinc.?finger|TALEN|ZFN|meganuclease)\b'),
    ("cell_therapy",  r'\b(?:CAR[-\s]?T\b|CAR[-\s]?NK\b|TIL\b|TCR[-\s]?T|autologous\s+cell|allogeneic\s+cell)\b'),
    ("mRNA",          r'\b(?:mRNA\s+therap|LNP\s+|lipid\s*nanoparticle|mRNA\s+vaccin)\b'),
]

# ─── DISEASE KEYWORDS ─────────────────────────────────────────────────────────
DISEASE_KEYWORDS = [
    ("duchenne_md",       r'\b(?:Duchenne|DMD)\b'),
    ("hemophilia",        r'\b(?:h[ae]mophilia|factor\s*VIII|factor\s*IX|FIX|FVIII)\b'),
    ("sma",               r'\b(?:SMA|spinal\s*muscular\s*atrophy|SMN)\b'),
    ("als",               r'\b(?:ALS|amyotrophic\s*lateral\s*sclerosis)\b'),
    ("parkinsons",        r'\bParkinson\b'),
    ("alzheimers",        r'\bAlzheimer\b'),
    ("retinal",           r'\b(?:retinal\s+dys|AMD|macular\s+degen|Stargardt|choroideremia|Leber|LCA|RPE65|retinitis\s+pigmentosa|ocular\s+gene)\b'),
    ("cancer",            r'\b(?:cancer|carcinoma|sarcoma|glioma|glioblastoma|lymphoma|leukemia|leukaemia|myeloma|melanoma|amyloidosis)\b'),
    ("sickle_cell",       r'\b(?:sickle.?cell|SCD|HbSS|sickle\s+cell\s+disease)\b'),
    ("thalassemia",       r'\b(?:thal[ae]ssemia|beta.?thal)\b'),
    ("huntington",        r'\bHuntington\b'),
    ("fabry",             r'\bFabry\b'),
    ("gaucher",           r'\bGaucher\b'),
    ("alpha1_at",         r'\b(?:alpha.?1\s*antitrypsin|A1AT|SERPINA1)\b'),
    ("pku",               r'\b(?:PKU|phenylketonuria)\b'),
    ("mps",               r'\b(?:MPS\s*[IVX]+|mucopolysaccharidosis|Hurler|Hunter|Morquio)\b'),
    ("amyloidosis",       r'\b(?:amyloidosis|transthyretin|TTR\s+amyloid|AL\s+amyloid)\b'),
    ("pompe",             r'\bPompe\b'),
    ("rett",              r'\bRett\b'),
    ("batten",            r'\bBatten\b'),
    ("scid",              r'\b(?:SCID|ADA-SCID|X-SCID|severe\s+combined\s+immuno)\b'),
    ("wiskott_aldrich",   r'(?:Wiskott.?Aldrich|WASp\s+(?:protein|gene|mutation|deficiency))'),
    ("wilson",            r"\bWilson'?s?\s*disease\b"),
    ("hemoglobin_e_beta", r'\bHbE.?beta\b'),
]

# ─── TIER 2 KEYWORD DICTS ────────────────────────────────────────────────────
# Values are normalized lowercase underscore labels (for consistent filtering).

ANIMAL_KEYWORDS = {
    r'\b(?:mouse|mice|murine)\b':               'mouse',
    r'\b(?:rat|rats|rodent)\b':                 'rat',
    r'\b(?:NHP|non.?human\s+primate|cynomolgus|macaque|rhesus|marmoset)\b': 'NHP',
    r'\b(?:dog|dogs|canine|beagle)\b':          'dog',
    r'\b(?:rabbit|rabbits)\b':                  'rabbit',
    r'\b(?:pig|pigs|porcine|swine|mini.?pig)\b': 'pig',
    r'\bferret\b':                              'ferret',
    r'\bzebrafish\b':                           'zebrafish',
    r'\b(?:sheep|ovine)\b':                     'sheep',
    r'\b(?:cat|cats|feline)\b':                 'cat',
}

CELL_LINE_KEYWORDS = {
    r'\bHEK\s*293\b':              'HEK293',
    r'\bJurkat\b':                 'Jurkat',
    r'\bVero\b':                   'Vero',
    r'\bA549\b':                   'A549',
    r'\bU87\b':                    'U87',
    r'\biPSC\b':                   'iPSC',
    r'\bprimary\s+neurons?\b':     'primary_neurons',
    r'\bcortical\s+neurons?\b':    'cortical_neurons',
    r'\bhepatocytes?\b':           'hepatocytes',
    r'\bHSC\b':                    'HSC',
    r'\bCHO\b':                    'CHO',
    r'\bK562\b':                   'K562',
    r'\bSf9\b':                    'Sf9',
    r'\bNSC\b':                    'NSC',
}

ROUTE_KEYWORDS = {
    r'\b(?:intravenous(?:ly)?|i\.v\.)\b':                                     'intravenous',
    r'\b(?:intramuscular(?:ly)?|i\.m\.)\b':                                  'intramuscular',
    r'\b(?:intrathecal(?:ly)?)\b':                                            'intrathecal',
    r'\b(?:intracranial(?:ly)?|ICV|intracerebroventricular)\b':              'intracranial',
    r'\bsubretinal\b':                                                        'subretinal',
    r'\bintrastriatal\b':                                                     'intrastriatal',
    r'\b(?:intraparenchymal(?:ly)?)\b':                                       'intraparenchymal',
    r'\b(?:intravitreal|intraocular)\b':                                      'intravitreal',
    r'\bsystemic(?:ally)?\b':                                                 'systemic',
    r'\b(?:oral(?:ly)?|per\s+os|by\s+mouth)\b':                              'oral',
    r'\b(?:subcutaneous(?:ly)?|(?<!\w)SC(?!\w)|s\.c\.)\b':                   'subcutaneous',
    r'\b(?:intraperitoneal(?:ly)?|(?<!\w)IP(?!\w)|i\.p\.)\b':                'intraperitoneal',
    r'\bintranasal\b':                                                        'intranasal',
}

METHOD_KEYWORDS = {
    r'\b(?:western\s+blot|immunoblot|WB)\b':                                 'western_blot',
    r'\b(?:IHC|immunohistochem)\b':                                          'IHC',
    r'\b(?:immunofluorescence|(?<!\w)IF(?!\w))\b':                           'immunofluorescence',
    r'\b(?:ELISA)\b':                                                        'ELISA',
    r'\b(?:flow\s+cytometry|FACS)\b':                                        'flow_cytometry',
    r'\b(?:qPCR|RT-PCR|quantitative\s+PCR|real.?time\s+PCR)\b':             'qPCR',
    r'\b(?:ddPCR|digital\s+PCR|droplet\s+digital)\b':                       'ddPCR',
    r'\b(?:PCR)\b':                                                          'PCR',
    r'\bhistolog\b':                                                         'histology',
    r'\b(?:FISH|fluorescence\s+in\s+situ)\b':                               'FISH',
    r'\b(?:RNA.?seq|RNAseq|transcriptom|RNA\s+sequencing)\b':               'RNA_seq',
    r'\b(?:scRNA.?seq|single.?cell\s+RNA)\b':                               'scRNA_seq',
    r'\bproteomics\b':                                                       'proteomics',
    r'\b(?:mass\s+spectrometry|mass\s+spec|LC.MS)\b':                       'mass_spectrometry',
    r'\bCRISPR\s+screen\b':                                                  'CRISPR_screen',
    r'\bluciferase\b':                                                       'luciferase_assay',
    r'\bbioluminescen\b':                                                    'bioluminescence',
    r'\bMRI\b':                                                              'MRI',
    r'\b(?:electrophysiology|patch.?clamp|MEA)\b':                          'electrophysiology',
    r'\b(?:NGS|next.?gen(?:eration)?\s+sequencing)\b':                      'NGS',
    r'\bSanger\s+sequencing\b':                                              'Sanger_sequencing',
    r'\b(?:WGS|whole.?genome\s+sequencing)\b':                              'WGS',
    r'\bconfocal\b':                                                         'confocal_microscopy',
    r'\b(?:electron\s+microscopy|cryo.?EM|TEM)\b':                          'electron_microscopy',
    r'\b(?:co.?IP|co.?immunoprecip)\b':                                     'co_IP',
    r'\bChIP.?seq\b':                                                        'ChIP_seq',
    r'\bATAC.?seq\b':                                                        'ATAC_seq',
    r'\bSouthern\s+blot\b':                                                  'Southern_blot',
    r'\bNorthern\s+blot\b':                                                  'Northern_blot',
    r'\b(?:TUNEL|terminal\s+deoxynucleotidyl)\b':                           'TUNEL',
}

ASSAY_KEYWORDS = {
    r'\b(?:transduction\s+effic|transduction\s+rate)\b':                   'transduction_efficiency',
    r'\b(?:vector\s+genome|genome\s+cop|VG\/|gc\/|vg\/mL|vg\/kg)\b':       'vector_genome_quantification',
    r'\b(?:capsid\s+titer|viral\s+titer|vector\s+titer|titre)\b':          'capsid_titer',
    r'\b(?:full\s+capsid|empty\s+capsid|capsid\s+content|full.?to.?empty)\b': 'capsid_fullness',
    r'\b(?:potency\s+assay|in\s+vitro\s+potency)\b':                       'potency_assay',
    r'\b(?:neutral[iy]z?ing\s+antibod|NAb[s]?|nAb[s]?)\b':                'neutralising_antibody',
    r'\b(?:cell\s+viability|viability\s+assay)\b':                         'cell_viability',
    r'\bbio.?distribution\b':                                               'biodistribution',
    r'\b(?:off.?target\s+edit|off.?target\s+effect)\b':                    'off_target_editing',
    r'\b(?:indel[s]?|insertion.?deletion)\b':                              'indel_rate',
    r'\b(?:editing\s+effic|editing\s+rate)\b':                             'editing_efficiency',
    r'\b(?:gene\s+expression|mRNA\s+expres|protein\s+expres|transgene\s+expres)\b': 'gene_expression',
    r'\bimmunogenicit\b':                                                   'immunogenicity',
    r'\btoxicolog\b':                                                       'toxicology',
    r'\bpharmacokinetic\b':                                                 'pharmacokinetics',
    r'\b(?:MRD|minimal\s+residual\s+disease)\b':                           'MRD_testing',
    r'\b(?:clonal\s+tracking|clonal\s+analysis|clonal\s+divers)\b':        'clonal_tracking',
}

INSTRUMENT_KEYWORDS = {
    r'\b(?:FACSAria|BD\s+FACS)\b':                    'BD_FACSAria',
    r'\b(?:Cytoflex|CytoFLEX)\b':                     'Cytoflex',
    r'\bSpectraMax\b':                                 'SpectraMax',
    r'\bNanoLuc\b':                                    'NanoLuc',
    r'\bTapeStation\b':                                'TapeStation',
    r'\bBioanalyzer\b':                                'Bioanalyzer',
    r'\bLightCycler\b':                                'LightCycler',
    r'\b(?:Nanodrop|NanoDrop)\b':                      'Nanodrop',
    r'\bSeahorse\b':                                   'Seahorse_XF',
    r'\b(?:10[x×]\s+Genomics|Chromium\s+Controller)\b': '10x_Genomics',
    r'\b(?:Illumina\s+(?:NovaSeq|HiSeq|MiSeq|NextSeq))\b': 'Illumina_sequencer',
    r'\b(?:Oxford\s+Nanopore|MinION|PromethION|ONT)\b': 'Oxford_Nanopore',
    r'\b(?:ambr\s*(?:15|250)|ambr15|ambr250)\b':       'ambr_bioreactor',
    r'\bAAVid\b':                                      'AAVid',
    r'\b(?:Octet|BLI\s+analysis|bio-layer)\b':         'Octet_BLI',
    r'\b(?:SEC.?MALS|MALS)\b':                         'SEC_MALS',
    r'\bNanoSight\b':                                  'NanoSight',
    r'\bClonoSEQ\b':                                   'ClonoSEQ',
}

# ─── TIER 2 INTELLIGENCE SIGNAL PATTERNS (v1-python upgrade) ─────────────────

STAGE_REGULATORY = re.compile(
    r'\b(?:BLA|NDA|MAA|FDA\s+approv|EMA\s+approv|approved\s+(?:therapy|product)|'
    r'market\s+authoriz|accelerated\s+approv|breakthrough\s+therapy\s+designation|'
    r'orphan\s+drug\s+designation)\b',
    re.IGNORECASE
)
STAGE_CLINICAL = re.compile(
    r'\b(?:Phase\s+(?:I{1,3}|1|2|3)|phase\s+[123]|first.?in.?human|first-in-human|'
    r'clinical\s+trial|IND\s+(?:application|filing|submission)|investigational\s+new\s+drug|'
    r'phase\s+I/II|NCT\d{4,}|ongoing\s+(?:clinical|trial))\b',
    re.IGNORECASE
)
STAGE_PRECLINICAL = re.compile(
    r'\b(?:preclinical|pre.?clinical|in\s+vitro|in\s+vivo|mouse\s+model|'
    r'murine\b|NHP\b|non.?human\s+primate|animal\s+model|rodent\s+model|'
    r'proof.?of.?concept|POC\b|bench.?scale)\b',
    re.IGNORECASE
)

INDUSTRY_ORG_RE = re.compile(
    r'\b(?:Therapeutics?|Pharmaceuticals?|Biosciences?|Biotechnology|Biotech\b|'
    r'Inc\.?|Corp\.?|GmbH|Ltd\.?\b|LLC\b|AG\b|BV\b|plc\b|Medicines\b|Genomics\b|'
    r'Biotherapeutics?|Sciences\b)\b',
    re.IGNORECASE
)
ACADEMIA_ORG_RE = re.compile(
    r'\b(?:University|Universit[äéè]|College\b|Institute\b|Hospital\b|'
    r'Medical\s+Center|Medical\s+Centre|School\s+of\b|Foundation\b|Academy\b|'
    r'Research\s+(?:Center|Centre)\b|INSERM\b|NIH\b|NCI\b|NHS\b|IRCCS\b)\b',
    re.IGNORECASE
)

IP_TERMS = {
    'patent':       re.compile(r'\b(?:patent(?:ed|ing)?|patent.?pending)\b', re.IGNORECASE),
    'proprietary':  re.compile(r'\bproprietary\b', re.IGNORECASE),
    'licensed':     re.compile(r'\b(?:licens(?:ed|ing|ee?)|exclusive\s+licen[sc]e|sublicens)\b', re.IGNORECASE),
    'orphan_drug':  re.compile(r'\borphan\s+drug\s+(?:designation|status)\b', re.IGNORECASE),
    'breakthrough': re.compile(r'\bbreakthrough\s+therapy\s+designation\b', re.IGNORECASE),
    'fast_track':   re.compile(r'\bfast.?track\s+(?:designation|status)\b', re.IGNORECASE),
}

AAV_CAPSID_RE = re.compile(
    r'\b(?:AAV[1-9]\b|AAV1[0-9]\b|AAVrh\d+|AAV-?PHP\b|AAVrh74\b|AAVhu68\b|'
    r'engineered\s+capsid|novel\s+capsid|self.?complementary\s+AAV|scAAV\b|'
    r'capsid\s+(?:engineer|variant|modif)|synthetic\s+capsid)\b',
    re.IGNORECASE
)

PAYLOAD_RE = re.compile(
    r'\b(?:SMN[12]?\b|dystrophin\b|micro-?dystrophin\b|'
    r'FIX\b|Factor\s*IX\b|FVIII\b|Factor\s*VIII\b|'
    r'RPE65\b|CEP290\b|RPGR\b|CNGB3\b|CNGA3\b|'
    r'beta.?globin\b|haemoglobin\b|hemoglobin\b|'
    r'PCSK9\b|LDLR\b|HTT\b|huntingtin\b|CFTR\b|'
    r'phenylalanine\s+hydroxylase|PAH\b|'
    r'alpha.?1.?antitrypsin\b|A1AT\b|'
    r'arginase\b|OTC\b|ornithine\s+transcarbamylase|'
    r'CLN[2-9]\b|MECP2\b|GBA\b|glucocerebrosidase\b|'
    r'ADA\b|adenosine\s+deaminase\b|'
    r'chimeric\s+antigen\s+receptor|TCR.?T\b)\b',
    re.IGNORECASE
)

TRIAL_ID_RE = re.compile(r'\bNCT\d{7,8}\b')

GMP_RE         = re.compile(r'\b(?:cGMP|current\s+GMP|GMP.?(?:grade|manufactured|batch|compliant))\b', re.IGNORECASE)
GMP_PROCESS_RE = re.compile(r'\b(?:process\s+development|CMC\b|manufacturing\s+readiness|technology\s+transfer|CDMO\s+partner)\b', re.IGNORECASE)
SCALE_RE       = re.compile(r'\b(?:scale.?up|clinical\s+scale|commercial\s+scale|manufacturing\s+scale)\b', re.IGNORECASE)

# Time point regex — captures durations mentioned in text
TIME_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*[-–]?\s*(\d+(?:\.\d+)?)?\s*'
    r'[-–\s]*(week|month|day|year|wk|mo|yr)s?\b',
    re.IGNORECASE
)

# Dose/titer regex — captures numeric doses with units
DOSE_RE = re.compile(
    r'(\d+(?:[.,]\d+)?(?:\s*[×xX]\s*10\^?\d+|\s*[Ee][+\-]?\d+)?)'
    r'\s*(vg|gc|genome\s*cop|vector\s*gen|IU|TU|cells?|particles?|µg|ug|mg|ng)'
    r'(?:\s*/\s*|\s+per\s+)'
    r'(kg|mL|ml|mouse|animal|dose|eye|brain|hemisphere)',
    re.IGNORECASE
)

# Country list for geography detection
COUNTRIES = [
    'United States', 'USA', 'Germany', 'United Kingdom', 'France',
    'Netherlands', 'Switzerland', 'Sweden', 'Italy', 'Spain', 'Australia',
    'Canada', 'Japan', 'China', 'South Korea', 'Israel', 'Belgium',
    'Denmark', 'Norway', 'Austria', 'Brazil', 'India', 'Singapore',
    'Ireland', 'Portugal', 'Poland', 'Czechia', 'Czech Republic',
]
COUNTRY_RE = re.compile(
    '|'.join(re.escape(c) for c in sorted(COUNTRIES, key=len, reverse=True)),
    re.IGNORECASE
)

# Sentence splitter
SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')


# ─── INTELLIGENCE SIGNAL HELPERS ─────────────────────────────────────────────

def detect_development_stage(text):
    if STAGE_REGULATORY.search(text):
        return 'regulatory'
    if STAGE_CLINICAL.search(text):
        return 'clinical'
    if STAGE_PRECLINICAL.search(text):
        return 'preclinical'
    return 'UNKNOWN'


def detect_sponsor_type(organisation, full_text):
    org = organisation if (organisation and organisation != 'UNKNOWN') else ''
    ind_org  = bool(INDUSTRY_ORG_RE.search(org))
    acad_org = bool(ACADEMIA_ORG_RE.search(org))
    if ind_org and acad_org:
        return 'collaborative'
    if ind_org:
        return 'industry'
    if acad_org:
        return 'academia'
    # Fallback: scan full text
    ind_text  = bool(INDUSTRY_ORG_RE.search(full_text))
    acad_text = bool(ACADEMIA_ORG_RE.search(full_text))
    if ind_text and acad_text:
        return 'collaborative'
    if ind_text:
        return 'industry'
    if acad_text:
        return 'academia'
    return 'UNKNOWN'


def detect_ip_signals(text):
    found = [key for key, rx in IP_TERMS.items() if rx.search(text)]
    return '|'.join(found) if found else 'UNKNOWN'


def detect_aav_capsids(text):
    hits = list(dict.fromkeys(m.group(0) for m in AAV_CAPSID_RE.finditer(text)))
    return '|'.join(hits[:8]) if hits else 'UNKNOWN'


def detect_payload(text):
    hits = list(dict.fromkeys(m.group(0) for m in PAYLOAD_RE.finditer(text)))
    return '|'.join(hits[:10]) if hits else 'UNKNOWN'


def detect_manufacturing_gmp(text):
    if GMP_RE.search(text):
        return 'gmp'
    if GMP_PROCESS_RE.search(text):
        return 'gmp_process_dev'
    if SCALE_RE.search(text):
        return 'scale_up'
    return 'UNKNOWN'


# ─── CORE EXTRACTION FUNCTIONS ───────────────────────────────────────────────

def norm_section_name(s):
    s = s.lower()
    if s.startswith('method'):  return 'Methods'
    if s.startswith('result'):  return 'Results'
    if s.startswith('intro'):   return 'Introduction'
    if s.startswith('concl'):   return 'Conclusion'
    return s.capitalize()


def split_sections(abstract_text):
    """Split abstract text into {preamble, Introduction, Methods, Results, Conclusion}."""
    boundaries = []
    for m in SECTION_RE.finditer(abstract_text):
        boundaries.append((m.start(), m.end(), norm_section_name(m.group(1))))

    if not boundaries:
        return {'preamble': abstract_text.strip()}

    sections = {'preamble': abstract_text[:boundaries[0][0]].strip()}
    for i, (start, end, name) in enumerate(boundaries):
        next_start = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(abstract_text)
        sections[name] = abstract_text[end:next_start].strip()
    return sections


def extract_title(preamble, title_first_line):
    """Recover multi-line title from preamble. Stops at author/affil lines."""
    STOP_RE = re.compile(
        r'^\d+[A-Z]'  # numbered affiliation: 1Asimov...
        r'|(?:University|Universit|Institute|Institut|Instit|College|Hospital|Center|Centre|GmbH|Inc\.\s|Corp\.\s|Ltd\.\s|Department|Dept\.)',
        re.IGNORECASE
    )
    AUTHOR_RE = re.compile(
        r'^[A-Z][a-zA-Z\.\-]+\d*,|^[A-Z]\.\s*[A-Z]\.'
    )

    parts = [title_first_line]
    for line in preamble.split('\n'):
        stripped = line.strip()
        if not stripped or stripped == title_first_line:
            continue
        if STOP_RE.match(stripped) or STOP_RE.search(stripped):
            break
        if AUTHOR_RE.match(stripped):
            break
        if len(parts) >= 3:
            break
        parts.append(stripped)
    return ' '.join(parts).strip()


def extract_authors(preamble):
    """Return (first_author, last_author, all_authors_raw, last_affil_num)."""
    AFFIL_RE = re.compile(
        r'^\d+[A-Z]'
        r'|(?:University|Universit|Institute|Institut|College|Hospital|Center|Centre|GmbH|Inc\.|Corp\.|Ltd\.|Department|Dept\.)',
        re.IGNORECASE
    )

    # Collect non-affiliation, non-blank lines from preamble (these are title/author lines)
    # Skip the abstract heading line (starts with the abstract ID: digits then space)
    candidate_lines = []
    for line in preamble.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^\d{1,4}\s+[A-Z]', stripped):
            continue  # skip the abstract heading (e.g. "4018 Comprehensive..." starts with ID then capital title)
        if AFFIL_RE.match(stripped) or AFFIL_RE.search(stripped):
            break  # once we hit affiliations, stop
        candidate_lines.append(stripped)

    if not candidate_lines:
        return 'UNKNOWN', 'UNKNOWN', 'not_reported', '1'

    # Identify author lines from candidate_lines.
    # A line is an author line if it has ≥2 commas (multi-author) OR ends with a
    # superscript digit run (last author in list, no trailing comma).
    # This distinguishes author lines from title-continuation lines.
    ENDS_WITH_SUPERSCRIPT = re.compile(r'[A-Za-z]\d+(?:\s+\d+)*\s*$')
    INITIAL_AUTHOR = re.compile(r'[A-Z]\.[A-Z]?\.\s+[A-Z]')
    author_lines = []
    for l in candidate_lines:
        has_multi_comma = l.count(',') >= 2
        has_trailing_sup = bool(ENDS_WITH_SUPERSCRIPT.search(l))
        has_initial      = bool(INITIAL_AUTHOR.search(l))
        if has_multi_comma or has_trailing_sup or has_initial:
            author_lines.append(l)

    if not author_lines:
        return 'UNKNOWN', 'UNKNOWN', 'not_reported', '1'

    raw_block = ' '.join(author_lines)

    # Strip superscript digit runs after letters: "Prendeville1" → "Prendeville", "Liang1 2 3" → "Liang"
    clean_block = re.sub(r'(?<=[A-Za-z])\d+(?:\s+\d+)*', '', raw_block)

    # Split on comma → individual name tokens
    tokens = [t.strip() for t in clean_block.split(',') if t.strip()]

    # Keep tokens that look like names (≥2 words OR initial+surname, starts uppercase)
    NAME_LIKE = re.compile(
        r'^[A-ZÀ-ɏ][a-zA-ZÀ-ɏ\-\']+(?:\s+[A-ZÀ-ɏ][a-zA-ZÀ-ɏ\-\'\.]+)+$'
    )
    names = [t for t in tokens if NAME_LIKE.match(t)]

    if not names and tokens:
        # last-resort: any token starting uppercase with >3 chars and containing a space (2+ words)
        names = [t for t in tokens if re.match(r'^[A-Z]', t) and ' ' in t and len(t) > 5]

    first = names[0] if names else 'UNKNOWN'
    last  = names[-1] if len(names) > 1 else first

    # Find last author's primary affiliation number from the raw (pre-stripped) block
    last_affil_num = '1'  # default
    if author_lines:
        raw_tokens = [t.strip() for t in raw_block.split(',') if t.strip()]
        if raw_tokens:
            last_raw = raw_tokens[-1]
            sup_m = re.search(r'(?<=[A-Za-z])\s*(\d+)', last_raw)
            if sup_m:
                last_affil_num = sup_m.group(1)

    return first, last, raw_block, last_affil_num


def extract_affiliation(preamble, preferred_affil_num='1'):
    """Return (organisation, geography) from the last author's numbered affiliation.

    Builds a map of all numbered affiliations (handles both same-line
    "1OrgA, City, Country, 2OrgB..." and multi-line formats), then picks the
    preferred affiliation number (derived from the last author's superscript).
    Line-wrapped country names (e.g. "United \\nKingdom") are rejoined by
    joining stripped continuation lines.
    """
    # Detect numbered affiliation markers: digit(s) immediately followed by a capital
    # letter, preceded by line-start/whitespace or a comma.
    # Distinguishes "1Asimov" (affil marker) from "AAV2" (no capital after digit).
    AFFIL_START = re.compile(
        r'(?:(?:^|\n)\s*|,\s*)(\d+)(?=[A-Z])',
        re.MULTILINE
    )
    all_matches = list(AFFIL_START.finditer(preamble))

    # Build map: affil_num → clean text (joining wrapped continuation lines)
    affil_map = {}
    for i, m in enumerate(all_matches):
        num = m.group(1)
        text_start = m.end()
        # End at the start of the next affiliation marker (not the text start — the
        # whole ", 2..." delimiter) so we don't include the next number itself.
        text_end = all_matches[i + 1].start() if i + 1 < len(all_matches) else len(preamble)
        raw_seg = preamble[text_start:text_end]
        parts = []
        for line in raw_seg.split('\n'):
            s = line.strip()
            if not s:
                break
            if re.match(r'^\d+[A-Z]', s):  # hit a new numbered affil on its own line
                break
            parts.append(s)
        affil_text = ' '.join(parts).rstrip(', ').strip()
        if num not in affil_map and affil_text:
            affil_map[num] = affil_text

    # Select: preferred (last author) → '1' (first) → any
    affil = (affil_map.get(preferred_affil_num)
             or affil_map.get('1')
             or next(iter(affil_map.values()), None))

    if not affil:
        # Fallback: institution keyword search in preamble
        for line in preamble.split('\n'):
            stripped = line.strip()
            if re.search(
                r'University|Universit|Institute|Hospital|Center|Centre|GmbH|Inc\.|Corp\.',
                stripped, re.IGNORECASE
            ):
                affil = stripped
                break
        if not affil:
            return 'UNKNOWN', 'UNKNOWN'

    # Geography = first country match (first in affil text = primary institution)
    cm = list(COUNTRY_RE.finditer(affil))
    geography = cm[0].group(0) if cm else 'UNKNOWN'

    # Organisation = text up to first comma
    parts = [p.strip() for p in affil.split(',')]
    organisation = parts[0] if parts else affil

    return organisation, geography


def detect_modality(text):
    for label, pattern in MODALITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return 'UNKNOWN'


def detect_diseases(text):
    found = []
    for label, pattern in DISEASE_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(label)
    return ','.join(sorted(set(found))) if found else 'UNKNOWN'


def match_keywords_list(text, kw_dict):
    """Match each pattern in kw_dict; return sorted comma-joined string or 'not_reported'."""
    found = set()
    for pattern, value in kw_dict.items():
        if re.search(pattern, text, re.IGNORECASE):
            found.add(value)
    return ','.join(sorted(found)) if found else 'not_reported'


def extract_time_points(text):
    seen = set()
    results = []
    for m in TIME_RE.finditer(text):
        # Normalize abbreviations before building the string (avoids double-sub on full words)
        raw_unit = m.group(3).lower()
        unit = {'wk': 'week', 'mo': 'month', 'yr': 'year'}.get(raw_unit, raw_unit)
        if m.group(2):
            tp = f"{m.group(1)}-{m.group(2)}{unit}"
        else:
            tp = f"{m.group(1)}{unit}"
        if tp not in seen and re.search(r'\d', tp):
            seen.add(tp)
            results.append(tp)
    return ','.join(results[:8]) if results else 'not_reported'


def extract_dose(text):
    """Extract up to 3 dose/titer values."""
    seen = set()
    results = []
    for m in DOSE_RE.finditer(text):
        dose_str = m.group(0).strip()
        if dose_str not in seen:
            seen.add(dose_str)
            results.append(dose_str)
    return ','.join(results[:3]) if results else 'not_reported'


def extract_what_found(results_text, conclusion_text=''):
    """Return (what_found, evidence_quote, confidence)."""
    if len(results_text) >= 50:
        source = results_text
        confidence = 'high'
    elif conclusion_text:
        source = conclusion_text
        confidence = 'medium'
    else:
        return 'UNKNOWN', 'UNKNOWN', 'low'

    source = source.strip()
    sentences = SENTENCE_RE.split(source)
    what_found = '. '.join(s.strip() for s in sentences[:2]).strip()
    if what_found and not what_found.endswith('.'):
        what_found += '.'

    words = source.split()
    evidence_quote = ' '.join(words[:25])

    return what_found or 'UNKNOWN', evidence_quote or 'UNKNOWN', confidence


# ─── MANIFEST LOADER ─────────────────────────────────────────────────────────

def load_manifest(manifest_path):
    """Returns dict: {chunk_txt_filename: 'pp.START-END'}."""
    page_map = {}
    p = Path(manifest_path)
    if not p.exists():
        return page_map
    with open(p, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            name = Path(row.get('chunk_txt', '')).name
            if name:
                page_map[name] = f"pp.{row['start_page']}-{row['end_page']}"
    return page_map


# ─── CHUNK PARSER ────────────────────────────────────────────────────────────

def parse_chunk(chunk_text, source_id, chunk_pages):
    """Yield one dict per abstract found in chunk_text."""
    # Find all valid abstract headings
    headings = []
    for m in HEADING_RE.finditer('\n' + chunk_text):  # prepend \n to catch first heading
        if is_valid_asgct_id(m.group('id')):
            headings.append(m)

    if not headings:
        return

    for i, m in enumerate(headings):
        abstract_id = m.group('id')
        title_first = m.group('title').strip()

        # Full abstract text (from this heading to next heading or EOF)
        abs_start = m.start()
        abs_end = headings[i + 1].start() if i + 1 < len(headings) else len('\n' + chunk_text)
        abstract_text = ('\n' + chunk_text)[abs_start:abs_end]

        sections = split_sections(abstract_text)
        preamble        = sections.get('preamble', '')
        methods_text    = sections.get('Methods', '')
        results_text    = sections.get('Results', '')
        conclusion_text = sections.get('Conclusion', '')

        # Title (may span lines in the preamble)
        title = extract_title(preamble, title_first)

        # Authors (returns 4-tuple: first, last, raw_block, last_affil_num)
        first_author, last_author, all_authors_raw, last_affil_num = extract_authors(preamble)

        # Affiliation — use last author's affiliation (senior/PI institution)
        organisation, geography = extract_affiliation(preamble, preferred_affil_num=last_affil_num)

        # Classification
        # Disease is scoped to Introduction + Conclusion: the sections where the disease
        # is the therapy target. Methods/Results may mention diseases as safety comparisons
        # (e.g. "proximity to cancer-associated genes") which would be false positives.
        disease_scope = sections.get('Introduction', '') + ' ' + conclusion_text
        modality = detect_modality(abstract_text)
        disease  = detect_diseases(disease_scope)

        # Main finding
        what_found, evidence_quote, confidence = extract_what_found(results_text, conclusion_text)

        # Tier 2 — conditions/methods (search methods + results for conditions)
        cond_text = methods_text + ' ' + results_text
        # Route is often stated in Introduction (model description), so search Methods + Intro
        route_text = methods_text + ' ' + sections.get('Introduction', '')
        animal_models  = match_keywords_list(cond_text, ANIMAL_KEYWORDS)
        cell_lines     = match_keywords_list(cond_text, CELL_LINE_KEYWORDS)
        route_of_admin = match_keywords_list(route_text, ROUTE_KEYWORDS)
        methods        = match_keywords_list(methods_text, METHOD_KEYWORDS)
        assays         = match_keywords_list(cond_text, ASSAY_KEYWORDS)
        instruments    = match_keywords_list(methods_text, INSTRUMENT_KEYWORDS)
        time_points    = extract_time_points(abstract_text)
        dose_raw       = extract_dose(abstract_text)

        # Tier 2 — intelligence signals
        development_stage        = detect_development_stage(abstract_text)
        sponsor_type             = detect_sponsor_type(organisation, abstract_text)
        ip_signals               = detect_ip_signals(abstract_text)
        aav_capsid               = detect_aav_capsids(abstract_text)
        therapeutic_payload      = detect_payload(abstract_text)
        trial_id_m               = TRIAL_ID_RE.search(abstract_text)
        trial_id                 = trial_id_m.group(0) if trial_id_m else 'UNKNOWN'
        manufacturing_gmp_signal = detect_manufacturing_gmp(abstract_text)

        citation = f"Abstract {abstract_id}. {title}. ASGCT 2026."

        yield {
            'abstract_id':       abstract_id,
            'title':             title,
            'chunk_pages':       chunk_pages,
            'first_author':      first_author,
            'last_author':       last_author,
            'organisation':      organisation,
            'geography':         geography,
            'modality':          modality,
            'disease':           disease,
            'what_found':        what_found,
            'evidence_quote':    evidence_quote,
            'confidence':        confidence,
            'schema_version':    'v1-python',
            'citation':          citation,
            'source_id':         source_id,
            'animal_models':     animal_models,
            'cell_lines':        cell_lines,
            'dose_raw':          dose_raw,
            'route_of_admin':    route_of_admin,
            'time_points':       time_points,
            'methods':           methods,
            'assays':            assays,
            'instruments':       instruments,
            'all_authors_raw':   all_authors_raw,
            'development_stage':        development_stage,
            'sponsor_type':             sponsor_type,
            'ip_signals':               ip_signals,
            'aav_capsid':               aav_capsid,
            'therapeutic_payload':      therapeutic_payload,
            'trial_id':                 trial_id,
            'manufacturing_gmp_signal': manufacturing_gmp_signal,
            'scientific_comment': '',
        }


# ─── RESCUE PASS ─────────────────────────────────────────────────────────────

def rescue_missing_abstracts(chunk_files, extracted_ids, row_counter, page_map,
                              csv_writer, csv_fh, jsonl_fh):
    """Second pass: find abstracts whose heading had no blank line before it.

    HEADING_RE requires blank lines; some abstracts immediately follow the prior
    abstract's last sentence with only a single newline.  This function searches
    for those specific IDs using a relaxed pattern (no blank-line guard), then
    wraps the found text with synthetic blank lines so parse_chunk() works normally.
    """
    valid_ids = {str(n) for lo, hi in VALID_RANGES for n in range(lo, hi + 1)}
    missing_ids = sorted(valid_ids - extracted_ids, key=lambda x: int(x))

    if not missing_ids:
        print("\nRescue pass: 0 missing IDs — nothing to do.")
        return row_counter

    print(f"\nRescue pass: searching for {len(missing_ids)} IDs not found in primary pass...")

    rescued = 0
    not_found = []

    for mid in missing_ids:
        # No blank-line requirement — just the ID at the start of a line
        rescue_re = re.compile(
            rf'(?:^|\n)[ \t]*{re.escape(mid)}[ \t]+(?P<title>[A-Z][^\n]{{9,}})',
            re.MULTILINE
        )

        found = False
        for chunk_path in chunk_files:
            try:
                chunk_text = chunk_path.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue

            m = rescue_re.search(chunk_text)
            if not m:
                continue

            # Abstract body: from heading to next HEADING_RE match or end of chunk
            next_m = HEADING_RE.search(chunk_text, m.end())
            if next_m and is_valid_asgct_id(next_m.group('id')):
                abstract_text = chunk_text[m.start():next_m.start()]
            else:
                abstract_text = chunk_text[m.start():]

            # Prepend blank lines so parse_chunk's HEADING_RE fires on this heading
            wrapped = '\n\n' + abstract_text.lstrip('\n')
            source_id   = chunk_path.name
            chunk_pages = page_map.get(source_id, 'UNKNOWN')

            for fact in parse_chunk(wrapped, source_id, chunk_pages):
                if fact['abstract_id'] != mid:
                    continue  # safety: only accept the target ID
                fact['row_id'] = row_counter
                row_counter += 1
                extracted_ids.add(mid)

                if csv_writer:
                    csv_writer.writerow(fact)
                    csv_fh.flush()
                if jsonl_fh:
                    jrow = {
                        'abstract_id':    mid,
                        'source_id':      fact['source_id'],
                        'source_type':    'pdf_abstract',
                        'fact_type':      'finding',
                        'subject':        fact['organisation'] if fact['organisation'] != 'UNKNOWN' else fact['first_author'],
                        'what':           fact['what_found'],
                        'modality':       fact['modality']      if fact['modality']      != 'UNKNOWN' else None,
                        'disease':        fact['disease']        if fact['disease']        != 'UNKNOWN' else None,
                        'organisation':   fact['organisation']   if fact['organisation']   != 'UNKNOWN' else None,
                        'geography':      fact['geography']      if fact['geography']      != 'UNKNOWN' else None,
                        'evidence_quote': fact['evidence_quote'],
                        'citation':       fact['citation'],
                        'confidence':     fact['confidence'],
                        'schema_version': 'v1-python',
                        # Tier 2 intelligence signals
                        'development_stage':        fact.get('development_stage')        if fact.get('development_stage')        != 'UNKNOWN' else None,
                        'sponsor_type':             fact.get('sponsor_type')             if fact.get('sponsor_type')             != 'UNKNOWN' else None,
                        'ip_signals':               fact.get('ip_signals')               if fact.get('ip_signals')               != 'UNKNOWN' else None,
                        'aav_capsid':               fact.get('aav_capsid')               if fact.get('aav_capsid')               != 'UNKNOWN' else None,
                        'therapeutic_payload':      fact.get('therapeutic_payload')      if fact.get('therapeutic_payload')      != 'UNKNOWN' else None,
                        'trial_id':                 fact.get('trial_id')                 if fact.get('trial_id')                 != 'UNKNOWN' else None,
                        'manufacturing_gmp_signal': fact.get('manufacturing_gmp_signal') if fact.get('manufacturing_gmp_signal') != 'UNKNOWN' else None,
                    }
                    jsonl_fh.write(json.dumps(jrow, ensure_ascii=False) + '\n')
                    jsonl_fh.flush()

                print(f"  Rescued {mid}: {fact['title'][:65]}")
                rescued += 1
                found = True
                break

            if found:
                break

        if not found:
            not_found.append(mid)

    if not_found:
        print(f"  Confirmed PDF gaps (not rescuable): {not_found}")
    print(f"Rescue pass complete: {rescued} rescued, {len(not_found)} confirmed gaps.")
    return row_counter


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='ASGCT 2026 abstract fact extractor')
    parser.add_argument('--chunk',       help='Single chunk .txt file (for testing)')
    parser.add_argument('--all-chunks',  help='Directory of chunk .txt files')
    parser.add_argument('--config',      required=True, help='Path to config.json')
    parser.add_argument('--out-csv',     help='Output CSV path (wide, all 26 cols)')
    parser.add_argument('--out-jsonl',   help='Output JSONL path (Supabase schema subset)')
    parser.add_argument('--dry-run',     action='store_true', help='Print 3 examples only, no write')
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))

    # Manifest → page ranges
    manifest_path = cfg_path.parent / 'chunks' / f"{cfg.get('prefix','ASGCT2026')}_split_manifest.csv"
    page_map = load_manifest(manifest_path)

    # Chunk files
    if args.chunk:
        chunk_files = [Path(args.chunk)]
    elif args.all_chunks:
        chunk_files = sorted(Path(args.all_chunks).glob('*.txt'))
    else:
        print("ERROR: --chunk or --all-chunks required", file=sys.stderr)
        sys.exit(1)

    if not chunk_files:
        print("ERROR: no .txt chunk files found", file=sys.stderr)
        sys.exit(1)

    print(f"Chunks to process: {len(chunk_files)}")

    # Resume: load already-extracted abstract_ids
    extracted_ids = set()
    if args.out_csv and not args.dry_run:
        out_csv = Path(args.out_csv)
        if out_csv.exists():
            with open(out_csv, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    extracted_ids.add(row.get('abstract_id', ''))
            print(f"Resume: {len(extracted_ids)} abstracts already extracted, will skip them")

    row_counter = len(extracted_ids) + 1

    # Open output files (append mode)
    csv_fh = jsonl_fh = None
    csv_writer = None

    if not args.dry_run:
        if args.out_csv:
            out_csv = Path(args.out_csv)
            out_csv.parent.mkdir(parents=True, exist_ok=True)
            csv_existed = out_csv.exists()
            csv_fh = open(out_csv, 'a', newline='', encoding='utf-8')
            csv_writer = csv.DictWriter(csv_fh, fieldnames=CSV_COLUMNS, extrasaction='ignore')
            if not csv_existed:
                csv_writer.writeheader()

        if args.out_jsonl:
            out_jsonl = Path(args.out_jsonl)
            out_jsonl.parent.mkdir(parents=True, exist_ok=True)
            jsonl_fh = open(out_jsonl, 'a', encoding='utf-8')

    total_new = 0
    total_skipped = 0
    dry_count = 0

    try:
        for chunk_path in chunk_files:
            try:
                chunk_text = chunk_path.read_text(encoding='utf-8', errors='replace')
            except Exception as e:
                print(f"  ERROR reading {chunk_path.name}: {e}", file=sys.stderr)
                continue

            source_id   = chunk_path.name
            chunk_pages = page_map.get(source_id, 'UNKNOWN')

            chunk_new = 0
            for fact in parse_chunk(chunk_text, source_id, chunk_pages):
                aid = fact['abstract_id']
                if aid in extracted_ids:
                    total_skipped += 1
                    continue

                fact['row_id'] = row_counter
                row_counter += 1
                extracted_ids.add(aid)
                total_new += 1
                chunk_new += 1

                if args.dry_run:
                    if dry_count < 3:
                        print(f"\n{'='*60}")
                        print(f"Abstract {fact['abstract_id']}: {fact['title'][:70]}")
                        for k in ['first_author','last_author','organisation','geography',
                                  'modality','disease','animal_models','cell_lines',
                                  'dose_raw','route_of_admin','time_points',
                                  'methods','assays','instruments','confidence']:
                            v = fact.get(k, '')
                            if v:
                                print(f"  {k:<18}: {str(v)[:80]}")
                        print(f"  {'what_found':<18}: {fact['what_found'][:100]}")
                        print(f"  {'evidence_quote':<18}: {fact['evidence_quote']}")
                    dry_count += 1
                else:
                    if csv_writer:
                        csv_writer.writerow(fact)
                        csv_fh.flush()
                    if jsonl_fh:
                        row = {
                            'abstract_id':    aid,
                            'source_id':      fact['source_id'],
                            'source_type':    'pdf_abstract',
                            'fact_type':      'finding',
                            'subject':        fact['organisation'] if fact['organisation'] != 'UNKNOWN' else fact['first_author'],
                            'what':           fact['what_found'],
                            'modality':       fact['modality']      if fact['modality']      != 'UNKNOWN' else None,
                            'disease':        fact['disease']        if fact['disease']        != 'UNKNOWN' else None,
                            'organisation':   fact['organisation']   if fact['organisation']   != 'UNKNOWN' else None,
                            'geography':      fact['geography']      if fact['geography']      != 'UNKNOWN' else None,
                            'evidence_quote': fact['evidence_quote'],
                            'citation':       fact['citation'],
                            'confidence':     fact['confidence'],
                            'schema_version': 'v1-python',
                            # Tier 2 intelligence signals
                            'development_stage':        fact.get('development_stage')        if fact.get('development_stage')        != 'UNKNOWN' else None,
                            'sponsor_type':             fact.get('sponsor_type')             if fact.get('sponsor_type')             != 'UNKNOWN' else None,
                            'ip_signals':               fact.get('ip_signals')               if fact.get('ip_signals')               != 'UNKNOWN' else None,
                            'aav_capsid':               fact.get('aav_capsid')               if fact.get('aav_capsid')               != 'UNKNOWN' else None,
                            'therapeutic_payload':      fact.get('therapeutic_payload')      if fact.get('therapeutic_payload')      != 'UNKNOWN' else None,
                            'trial_id':                 fact.get('trial_id')                 if fact.get('trial_id')                 != 'UNKNOWN' else None,
                            'manufacturing_gmp_signal': fact.get('manufacturing_gmp_signal') if fact.get('manufacturing_gmp_signal') != 'UNKNOWN' else None,
                        }
                        jsonl_fh.write(json.dumps(row, ensure_ascii=False) + '\n')
                        jsonl_fh.flush()

            if not args.dry_run:
                print(f"  {source_id}: {chunk_new} new abstracts extracted")

        # Rescue pass: pick up abstracts with no blank line before their heading
        if not args.dry_run and (args.out_csv or args.out_jsonl):
            row_counter = rescue_missing_abstracts(
                chunk_files, extracted_ids, row_counter, page_map,
                csv_writer, csv_fh, jsonl_fh,
            )

    finally:
        if csv_fh:
            csv_fh.close()
        if jsonl_fh:
            jsonl_fh.close()

    if args.dry_run:
        print(f"\nDRY RUN complete: found {dry_count} abstracts across {len(chunk_files)} chunk(s). Nothing written.")
    else:
        print(f"\nDone: {total_new} new rows extracted, {total_skipped} skipped (already done)")
        if args.out_csv:
            print(f"CSV output:  {args.out_csv}")
        if args.out_jsonl:
            print(f"JSONL output: {args.out_jsonl}")


if __name__ == '__main__':
    main()
