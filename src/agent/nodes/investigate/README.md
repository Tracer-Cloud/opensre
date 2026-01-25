# Investigation Notes For Humans 
- Previously we had split the hypothesis generation and hypothesis execution nodes
- This proved to be too complex early on, in particular when applying self-reflection to counter hallucinations 
- That is why we should first build a solid context and meta data of the actions that the LLM can take, and improve the interpretation of those actions. 



# Roadmap Extensions 
- Valid data analysis (when the agent receive 8 billion % RAM usage it thinks that there is too much RAM being used, rather than questioning the unit of the value that it receives from the API)
- Self reflection step to improve accuracy.
