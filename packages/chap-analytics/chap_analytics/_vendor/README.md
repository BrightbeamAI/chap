# Vendored runtime

The standalone report inlines these so it opens from a file share with no
network. They are unmodified release builds, kept here so a report is one file.

| File | Package | Version | Licence |
|---|---|---|---|
| vega.min.js | vega | 6.4.0 | BSD-3-Clause (LICENSE.vega) |
| vega-lite.min.js | vega-lite | 6.4.3 | BSD-3-Clause (LICENSE.vega-lite) |
| vega-embed.min.js | vega-embed | 7.2.0 | BSD-3-Clause (LICENSE.vega-embed) |

`render.js` is this package's own script: given Vega-Lite specifications on
standard input, it writes SVG through the bundles above under Node, which is
how the tests check that every chart compiles and how `Chart.save` writes
SVG where vl-convert is absent.
