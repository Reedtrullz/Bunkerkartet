# GPX 1.1 schema fixture

- Source: <https://www.topografix.com/GPX/1/1/gpx.xsd> (official TopoGrafix GPX 1.1 schema).
- Retrieved: 2026-10-03.
- SHA-256: `9e4d1988b862edbe556305b130f8f6f1b29864fefd0dc02d5dab04ccdd1f34d6`.
- License: the XSD does not contain a standalone license declaration. Its copyrightType documentation gives an example of copyright metadata for GPX content; that example is not schema attribution. TopoGrafix describes GPX as an open standard; its separate statement that web-page text is public domain does not expressly license this XSD. This fixture is retained verbatim as a local validation reference, with the rights status recorded here rather than inferred.

Validate generated output locally with:

```sh
xmllint --noout --schema tests/fixtures/gpx/gpx-1.1.xsd route.gpx
```
