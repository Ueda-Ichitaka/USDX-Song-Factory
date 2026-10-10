hyph_fi.dic - Finnish hyphenation patterns for pyphen

About me: pyphen ships no Finnish dictionary (LibreOffice has none either),
so hyphenation.py loads this file for language "fi". It is the Finnish TeX
pattern list converted to the hyphen/pyphen (.dic) format: the line "UTF-8"
followed by the 286 patterns, unchanged.

Source:    tex-hyphen project, hyph-utf8 package
           https://github.com/hyphenation/tex-hyphen
           hyph-utf8/tex/generic/hyph-utf8/patterns/txt/hyph-fi.pat.txt
           at commit 5684c0f51c0b81133db2efbe60a408b4155a3ff5
           (sha256 of the pattern file:
           ccabb0b3f403eca9999c0cd73b75d16b0220f2052d8f6aad2f98b5f1854dc027)
Title:     Hyphenation patterns for Finnish, version 2.2
Copyright: Copyright (C) 1986, 1988, 1989 Kauko Saarinen
           (Computing Centre, University of Jyvaskyla, Finland)
Licence:   Patterns may be freely distributed
Hyphenmin: left 2, right 2 (pyphen's defaults)

Conversion:
    (echo UTF-8; cat hyph-fi.pat.txt) > hyph_fi.dic
