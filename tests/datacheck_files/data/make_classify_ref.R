# Reference values for tests/datacheck_files/test_classify.py (run from this
# directory with the metacheck reference R):  Rscript make_classify_ref.R
# The Python tests stub file_category()/filetype() (ported in pytacheck.fileinfo)
# with the recorded values and compare the rest of the classification to R.
suppressPackageStartupMessages(library(metacheck))
names <- c("data.csv", "analysis.R", "README.md", "codebook.xlsx", "photo.png",
           "experiment.psyexp", "notes.pdf", "archive.zip", "sample.fasta",
           "sample.fasta.gz", "random_archive.tar.gz", "sample.dat.gz", "Results.docx",
           "ro-crate-metadata.json", "LICENSE", "survey.qsf", "model.stan", "run.sbatch",
           "prereg.pdf", "code_book.csv", "metadata.csv", "stimuli list.txt", "x.jasp",
           "study.omv", "data.por", "output.out", "notes", ".gitignore", "plot.svg",
           "Materials - Exp 2.csv", "readme.xls", "my_output_log.html",
           "Informant Survey_Redacted.pdf", "slides.pptx", "x.pdf", "notes.txt", "clip.mp4",
           "a.pdf", "b.txt", "DESCRIPTION", "NAMESPACE", "fit.R", "fit.Rd", "t.R", "raw.csv",
           "study_data.csv", "proj.Rproj", "BaBA.Rproj", "Flowchart.png", "data.sav",
           "trial.edf", "scan.nii.gz", "seq.bam", "reads.fastq", "genome.vcf", "x.RData",
           "script.do", "tbl.tsv", "book.ods", "doc.odt", "img.tiff", "movie.mov", "audio.wav",
           "report.html", "page.htm", "x.json", "x.xml", "x.yaml", "x.txt", "x.log",
           "Codebook.pdf", "data_dictionary.xlsx", "variables.csv", "manifest.csv", "x.ipynb",
           "x.Rmd", "x.qmd", "x.py", "x.m", "x.sps", "x.sas", "x.dta", "x.mat", "x.h5",
           "x.feather", "x.parquet", "x.rds", "x.dat", "x.sqlite", "x.db", "x.7z", "x.tar",
           "x.gz", "x.tgz", "x.rar", "x.psyexp", "x.osexp", "x.iqx", "x.e3", "x.ebs2",
           "x.opensesame", "x.exe", "x.dll", "sample.fa", "sample.fq", "sample.fastq",
           "sample.fa.gz", "sample.fq.gz", "sample.fastq.gz", "random.gz")
paths <- c("ResearchBox 801/Materials/Informant Survey_Redacted.pdf", "Output/slides.pptx",
           "Materials/data.csv", NA, "", "study/Data/clip.mp4", "Scripts/a.pdf", "prereg/b.txt")
path_names <- c("Informant Survey_Redacted.pdf", "slides.pptx", "data.csv", "x.pdf",
                "notes.txt", "clip.mp4", "a.pdf", "b.txt")
pkg_sets <- list(
  root_with_study = c("DESCRIPTION", "NAMESPACE", "R/fit.R", "man/fit.Rd", "tests/t.R",
                      "analysis.R", "data/raw.csv", "study_data.csv", "proj.Rproj"),
  root_only = c("DESCRIPTION", "NAMESPACE", "R/fit.R", "README.md", "LICENSE",
                "BaBA.Rproj", "Flowchart.png"))
ref <- list(
  names = names,
  file_category = file_category(names)$file_category,
  filetype = unname(filetype(names)),
  classify = data_classify_files(names),
  doc_role = metacheck:::.data_doc_role(names),
  path_names = path_names,
  paths = paths,
  classify_paths = data_classify_files(path_names, paths),
  pkg_sets = pkg_sets,
  pkg = lapply(pkg_sets, metacheck:::.is_r_package_file)
)
jsonlite::write_json(ref, "classify_ref.json", auto_unbox = FALSE, na = "null", pretty = TRUE)
