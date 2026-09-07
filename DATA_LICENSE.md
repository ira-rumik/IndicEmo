# Data and third-party materials

The Apache-2.0 license for the evaluation suite does not automatically apply to
downloaded benchmark archives or outputs generated through external APIs.

| Material | Scope |
| --- | --- |
| Rumik-authored evaluation rubric, schemas, and generation configurations committed in this repository | Apache-2.0, like the suite's code and documentation |
| Original transcripts and frozen requests under `benchmark/` | Rumik-authored material licensed under Apache-2.0 |
| Numeric judgments, result CSVs, and provenance under `benchmark/` | Rumik's rights, if any, in this compilation are licensed under Apache-2.0; no rights to underlying provider models, audio, or omitted response prose are granted |
| Benchmark transcripts and archived experiment data downloaded by the suite | Separate artifacts; no additional license is granted to them by the suite's code license |
| Provider-generated audio and raw judge responses | Excluded from the code license; applicable provider agreements and third-party rights must be checked separately |
| Model weights, voice assets, and reference recordings | No rights are granted by this repository's license |
| Company logos embedded in result figures | Excluded from the Apache-2.0 grant; see NOTICE |

Running a generation script with an API key does not transfer ownership of the
provider's model or voice. Likewise, the availability of a downloadable recording
does not itself establish permission to redistribute, sublicense, or use it for
training. This document records the scope of our grant; it does not add restrictions
to the Apache-2.0-licensed software or override third-party agreements.

The standalone bundle contains no audio or judges' free-text responses. API-based
generation requires the user's own access and compliance with applicable terms.
