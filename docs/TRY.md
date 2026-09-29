# Try metacheck

This is a preview of metacheck in Python. It runs on your own computer: you give it a paper, and it checks the paper and writes a report.

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
