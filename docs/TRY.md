# Try metacheck

This is a preview of metacheck in Python. It runs on your own computer: you give it a paper, and it checks the paper and writes a report.

If you were sent a link to a hosted copy, you can open that link instead and install nothing. It runs the same app on a server.

## Install it

**On a Mac or Linux.** Open Terminal and paste this line:

```sh
curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh
```

If it says `curl` is not found, use this line instead:

```sh
wget -qO- https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh
```

**On Windows.** Open PowerShell and paste this line:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex"
```

If it says `Could not create SSL/TLS secure channel`, paste this line in the PowerShell window that is already open:

```powershell
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex
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

About PDFs: "Read PDFs with" lets you choose how a PDF is turned into text.

- **GROBID** is the default. It is the public server at TU Eindhoven (https://grobid.hti.ieis.tue.nl). R metacheck uses the same server. So a PDF you check is sent there. If that server does not answer, the PDF goes to a public GROBID server hosted on Hugging Face instead.
- **bibr** is the scienceverse service. It needs a key. The person who sent you the link can give you one. Paste it into the box "Your bibr key". Tick "Remember the key on this computer" if you want to type it only once. The key is saved in a file that only you can read, and it is sent to the bibr service only. If bibr does not work, the app tells you. It does not switch to GROBID by itself, because you chose where the PDF goes.

XML and JSON files stay on your computer.

About data files: the box "Check the shared data files" is ticked. When the paper links to data files on OSF or other repositories, the app downloads them and checks them. This can take a few minutes. The other results appear first, and the data check is added when it is done. A line under the results shows that it is still running. Press "Stop the data check" if you do not want to wait. The other results stay on the page. Downloaded files are kept on your computer, so a second run is faster. Untick the box to skip this check.

## Which checks run

- Five checks are validated. The app says this about them: "Error rates as published in the documentation of R metacheck 0.3.1. The R version and PDF pipeline they were measured with are not recorded. Not yet re-measured on this version."
- A set of experimental checks also runs. Each one is labelled as experimental.
- The data check is experimental too. It runs last, unless you untick its box.
- The online checks are optional. They are slower, because they look things up on other servers.

To run other checks, open "Choose the checks" and tick the ones you want. The ticks start as the checks above, and the online box ticks or unticks the online checks. The list holds every check that takes seconds and needs no key, the new ones from bibr (figures and tables referred to, author information, license) among them. Checks that take minutes or need a key or a server of their own are not in the list; run them with the `pytacheck` command. If a pack you installed has a preset for papers, "Start from" lists it, and choosing it ticks its checks (and the data box, if the preset has the data check). A shared server lists only presets made of built-in checks.

To open the app with a preset already chosen, add `--preset NAME` to the install line (`sh -s -- --preset NAME`; on Windows set `METACHECK_APP_PRESET=NAME` first), or run `metacheck-app --preset NAME`. A data package preset opens the "Check a data package" page with it chosen.

## Check a data package

The data package of a paper, the folder of data, code and documentation that comes with it, is checked too. Drop its zip into the same box as the paper, or type the link of its public repository on GitHub, GitLab, OSF or Zenodo there: the page checks the files, the folder tree and the README, and shows a checklist. The app downloads a repository link to your computer, and nothing else leaves it. A shared server takes a zip of up to 50 MB or a link too, and deletes the package after the run. See [DATAPACKAGE.md](DATAPACKAGE.md) for what is checked and what a link may do.

The app also has a second page, "Check a data package" (the link at the top right of the page). It takes a folder, a zip or a link, and runs on your computer only. A shared server does not have this page.

The folder must be inside your home folder. For a package somewhere else, upload it as a zip or give its link. The box "Name the concept of each data column with the local classifier" is ticked. The install above does not include the classifier, so the page then names the concepts by rules only, and says so under the results. [DATAPACKAGE.md](DATAPACKAGE.md#in-the-app) explains the classifier and how to allow other folders.

To open the app on that page, run the install line with `--steward` (`sh -s -- --steward`) or, on Windows, with `METACHECK_STEWARD=1` set first (the same way as for the uninstall line). Or run `metacheck-app --page package`.

## Open it again

Run the same install line again. It takes a few seconds the second time. You can also run the app from its folder: `metacheck-app` in the `bin` folder.

If the first window is still open, the app is still running. Use the link printed in that window, and close it before you run the install line again.

## Remove it

**On a Mac or Linux:**

```sh
curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh -s -- --uninstall
```

Without `curl`:

```sh
wget -qO- https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh -s -- --uninstall
```

**On Windows:**

```powershell
$env:METACHECK_UNINSTALL=1
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.ps1 | iex"
Remove-Item Env:METACHECK_UNINSTALL
```

The last line clears the setting, so that the install line works again.

This removes the folder above. The app's own settings and state file stay where they are. The uninstall message tells you where.

## Something wrong?

Please [open an issue](https://github.com/scienceverse/pytacheck/issues). Say what you did and what you saw.

## Credit

pytacheck is a translation of metacheck by Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and contributors. Please cite metacheck when you use it.
