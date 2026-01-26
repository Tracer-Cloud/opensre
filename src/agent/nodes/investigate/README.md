# Investigation Notes For Humans 
- Previously we had split the hypothesis generation and hypothesis execution nodes
- This proved to be too complex early on, in particular when applying self-reflection to counter hallucinations 
- That is why we should first build a solid context and meta data of the actions that the LLM can take, and improve the interpretation of those actions. 

# Roadmap Extensions 
- Valid data analysis (when the agent receive 8 billion % RAM usage it thinks that there is too much RAM being used, rather than questioning the unit of the value that it receives from the API)
- Self reflection step to improve accuracy.

# Notes about pre-refactoring (depriacted notes)
@todo@@@@@@@@@@@@@@@@@@@@@@@@
This code is very wrong!
Because it always leads to 3 different hypothesis that are being executed with a minimum hypothesis in each source
I suspect we will be much more accurate if we structure our investigations as available actions, 
and provide those actions with rich meta data descriptions of inputs and outputs

Another big problem right now is that these available sources are delivered into the prompt construction of the investigation, 
without knowing in terms of input and output what these tools are actually good for

What we should do is:
- Create a get available actions function that is centralized and can be called by all nodes 
- Provides rich meta data so that the LLM knows in more detail what it actually does. 

In addition we should split node_investigate into 2-3 different stages:
1. Prompt construction (separate sister file) that also contains the get available actions
2. Investigation execution (separate sister file) 
3. Post processing (merging of evidence, hypothesis tracking etc.)
