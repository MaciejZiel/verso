# Submitting Verso to Flathub

`io.github.maciejziel.Verso.yml` here is the manifest for the Flathub
repository: the one in the repository root, with Verso built from a release
tag instead of the working tree. Regenerate it for each release:

```bash
tools/flathub_manifest.py v1.0.0
```

## Check before submitting

```bash
# lint the manifest and the metainfo, as Flathub's CI does
flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest packaging/flathub/io.github.maciejziel.Verso.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder appstream data/io.github.maciejziel.Verso.metainfo.xml.in

# build from the tag on GitHub and lint the resulting repository
flatpak run org.flatpak.Builder --force-clean --user --install-deps-from=flathub \
  --repo=repo builddir packaging/flathub/io.github.maciejziel.Verso.yml
flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
```

Checked for v1.0.0 (2026-11-05): the manifest and metainfo lints pass, the
build from the tag succeeds, and the repository lint reports only
`appstream-external-screenshot-url` and
`appstream-screenshots-not-mirrored-in-ostree`. Those two come from a local
build: Flathub's builder mirrors the screenshots to
`https://dl.flathub.org/media` itself (`--mirror-screenshots-url`).

## Submission

1. Fork <https://github.com/flathub/flathub> and clone the `new-pr` branch.
2. Copy `io.github.maciejziel.Verso.yml` into the root of the clone, commit
   and push.
3. Open a pull request against `new-pr` titled `Add io.github.maciejziel.Verso`
   with the text below.
4. Comment `bot, build` on the pull request for a test build.
5. After the merge, verify the app on <https://flathub.org/developers>: an
   `io.github.<user>` ID is verified through the GitHub account that owns the
   repository.

### Pull request text

> Verso is a GTK 4 / libadwaita text editor with live previews for Markdown
> (KaTeX, Mermaid, footnotes), JSON/YAML/TOML/XML/INI trees, CSV tables, HTML
> and SVG, print and PDF export, session restore, `.editorconfig` support and
> a focus mode. Source: https://github.com/MaciejZiel/verso (MIT).
>
> **Permissions**
>
> - `--filesystem=home`: Verso is a general-purpose text editor. Opening a
>   file through the portal gives access to that one file only, but the
>   Markdown preview loads images and follows links relative to the file's
>   folder (`![](images/diagram.png)`, `[next](chapter-2.md)`), pasted images
>   are saved to an `images/` folder next to the document, `.editorconfig`
>   files are read from the parent folders, and the open file is watched for
>   changes on disk. None of this works through the document portal. Other
>   editors on Flathub ask for more: GNOME Text Editor, gedit, Apostrophe,
>   MarkText and Typora use `--filesystem=host`; Verso limits itself to the
>   home folder.
> - `--device=dri`: the previews are WebKitGTK views, which need GPU access
>   for acceptable rendering performance.
> - `--share=ipc`, `--socket=wayland`, `--socket=fallback-x11`: the usual GUI
>   permissions.
>
> No network access is requested: scripts in HTML previews are off by
> default and the previews load only local files.
