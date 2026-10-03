# Contributing

Thanks for helping! Everyone is welcome - you don't need to write code.

## Share feedback and ideas

- **Idea or suggestion:** open an *Idea / suggestion* issue (in the app: Help > Suggest an idea...). Rough ideas are fine;
  say what you'd like to do and why. Ideas are discussed on the issue, and good ones get a `planned` label.
- **Something broke:** open a *Bug report* (Help > Report a bug...). Include your version (Help > About), what you did, what
  you expected and what happened. Paste the relevant lines from the Status window or `logs/Status.txt` - but remove anything
  private first (API keys, node keys).
- **Questions / show-and-tell:** use the issue tracker (or GitHub Discussions if the maintainers have enabled it).

## Contribute an addon

Addons are the easiest way to add a feature without touching the core. Read [docs/ADDONS.md](docs/ADDONS.md). In short:
write it, run `python packages/check_package.py packages/<name>`, then open an *Addon submission* issue or a pull request.
Submitted addons are reviewed and tested by a maintainer; tested ones are added to `addons-catalog.json`, which is the list
people choose from and download inside the app.

## Contribute code or docs

1. Fork the repo and create a branch.
2. Run the GUI in demo mode (`python mcIRC.py --demo`) to check your change without a radio. If you changed anything that
   talks to the node, say how you tested it on real hardware.
3. Keep changes focused; one pull request per fix or feature. Match the surrounding code style.
4. Never commit secrets, `gui_settings.json`, `nodes.db`, `logs/`, `backup/` or installed addons (`.gitignore` covers these).
5. Open a pull request using the template and describe what it does and how you tested it.

## Ground rules

- Be kind and constructive. Disagree with the idea, not the person.
- Don't add anything that makes the bot transmit more than it has to: the mesh is a shared, low-bandwidth resource.
- By contributing you agree your work is released under the project's [MIT license](LICENSE).
