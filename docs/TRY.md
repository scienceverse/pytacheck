# Try metacheck

This is a preview of metacheck in Python. It runs on your own computer: you give it a paper, and it checks the paper and writes a report.

## Install it

**On a Mac or Linux.** Open Terminal and paste this line:

```sh
curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh
```

**On Windows.** Open PowerShell and paste this line:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex"
```

## What happens

- The script downloads uv (a small tool that manages Python), Python and the app.
- It puts all of them in one folder. It changes nothing else on your computer: no shell profile, no PATH, no registry entry.
- The first time takes about 1 to 2 minutes.
- The app opens in your browser.
- Keep the Terminal or PowerShell window open while you use the app. Close the window to stop it.

The folder is:

| System | Folder |
|---|---|
| Linux | `~/.local/share/metacheck` |
| macOS | `~/.local/share/metacheck` |
| Windows | `%LOCALAPPDATA%\metacheck` |

## Use it

Drop in a PDF, a GROBID XML file or a bibr JSON file. Or try the demo paper.

About PDFs: a PDF is turned into text by the public GROBID server at TU Eindhoven (https://grobid.hti.ieis.tue.nl). R metacheck uses the same server. So a PDF you check is sent there. XML and JSON files stay on your computer.

## Which checks run

- Five checks are validated. Their error rates are shown next to them: "Error rates as published in the documentation of R metacheck 0.3.1. The R version and PDF pipeline they were measured with are not recorded. Not yet re-measured on this version."
- A set of experimental checks also runs. Each one is labelled as experimental.
- The online checks are optional. They are slower, because they look things up on other servers.

## Open it again

Run the same install line again. It takes a few seconds the second time. You can also run the app from its folder: `metacheck-app` in the `bin` folder.

## Remove it

**On a Mac or Linux:**

```sh
curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh -s -- --uninstall
```

**On Windows:**

```powershell
$env:METACHECK_UNINSTALL=1
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex"
Remove-Item Env:METACHECK_UNINSTALL
```

The last line clears the setting, so that the install line works again.

This removes the folder above. The app's own settings stay where they are. The uninstall message tells you where.

## Something wrong?

Please [open an issue](https://github.com/scienceverse/pytacheck/issues). Say what you did and what you saw.

## Credit

pytacheck is a translation of metacheck by Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and contributors. Please cite metacheck when you use it.
