# Security Policy

## Supported versions

Only the latest released tag receives fixes. Pin a released `v*` tag; `main`
is unstable.

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do **not** open a public
issue for a security report.

- Preferred: open a private advisory via GitHub's **Security → Report a
  vulnerability** tab on this repository.
- Fallback: e-mail the maintainer at the address listed under `authors` in
  `pyproject.toml`, with `gopro-ingest security` in the subject.

Please include the affected version, a description of the issue and its
impact, reproduction steps, and any suggested remediation.

## What to expect

- Acknowledgement within 5 business days.
- Initial assessment and severity triage within 10 business days.
- Coordinated disclosure: we agree a timeline with you before any public
  write-up, and credit reporters who want it.

## Scope and threat model

This library reads two kinds of untrusted input.

**GPX files** are XML from cameras, phone apps, and other people's folders.
They are parsed through `defusedxml`, which refuses entity expansion and
external-entity resolution. A parser bypass, or a way to make `parse_gpx_file`
read a file outside the path it was handed, is in scope.

**Video containers** are read only through `ffprobe`, which is invoked as an
argument list — never through a shell — with a timeout. `ffprobe` itself is
out of scope; report those to the FFmpeg project. A way to make this library
invoke something other than `ffprobe`, or to inject arguments into that
invocation, is in scope.

**Location data is the sensitive payload here.** GPS tracks and telemetry
extracted by this library describe where somebody physically was. A defect
that causes coordinates to be written somewhere unexpected — logged at a
level a caller would not expect, left in a temporary file, or embedded in an
error message that ends up in a bug tracker — is a security issue, not a
cosmetic one, and we would like to hear about it.

Out of scope: the optional `gopro-overlay` dependency (report upstream),
`ffmpeg` and `ffprobe` themselves, and anything a caller does with the GPX
files this library produces.
