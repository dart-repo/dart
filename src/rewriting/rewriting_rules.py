def rewrite_scope_ambiguity(question, scope_map):
    rewritten = []

    for token, interps in scope_map.items():
        if len(interps) < 2:
            continue
        
        if token not in question:
            continue

        token_words = token.split()
        scope_words = ["each", "every", "all"]
        scope_word = next((w for w in scope_words if w in token_words), None)
        if not scope_word:
            continue

        entity = " ".join(token_words[token_words.index(scope_word) + 1:])
        has_for = f"for {token}" in question
        full_token = f"for {token}" if has_for else token

        # e.g., Show me the jobs each startup has
        for interp in interps:
            # the jobs each startup has individually
            if interp.startswith(f"for {scope_word}") or interp.startswith("for each"):
                sub = f"for each {entity} individually"
                
            # the jobs common to all startups
            elif interp.startswith("common to all"):
                sub = " ".join(f"common to all {' '.join(entity.split()[:-1])} {interp.split()[-1]}".split())
            else:
                sub = interp
            rewritten.append(question.replace(full_token, sub))

    return rewritten

def rewrite_attach_ambiguity(question, attach_map):
    rewritten = []

    for modifier, interps in attach_map.items():
        if len(interps) < 2:
            continue

        narrow, broad = interps[0], interps[-1]

        full_token = f"{broad} {modifier}"
        modifier_first = False
        if full_token not in question:
            full_token = f"{modifier} {broad}"
            modifier_first = True
            if full_token not in question:
                continue
        
        used_conj = next((c for c in [" and ", " or "] if c in broad), None)
        if used_conj is None:
            continue

        nouns = [n.strip() for n in broad.split(used_conj) if n.strip()]
        if len(nouns) < 2:
            continue

        other_nouns = [n for n in nouns if n != narrow]

        if modifier_first: # pre-coordination; Show part-time servers and chefs.
            narrow_rewrite = question.replace(
                full_token,
                f"all {f'{used_conj}all '.join(other_nouns)}{used_conj}{modifier} {narrow}"
            ) # Show all chefs and part-time servers.
            broad_rewrite = question.replace(
                full_token,
                f"{used_conj}".join(f"{modifier} {n}" for n in nouns)
            ) # Show part-time chefs and part-time servers.
            
        else: # post-coordination; Show films and novels released in 2020.
            narrow_rewrite = question.replace(
                full_token,
                f"{narrow} {modifier},{used_conj}all {f'{used_conj}all '.join(other_nouns)}"
            ) # Show novels released in 2020, and all films.
            broad_rewrite = question.replace(
                full_token,
                f"{used_conj}".join(f"{n} {modifier}" for n in nouns)
            ) # Show films released in 2020 and novels released in 2020

        rewritten.extend([narrow_rewrite, broad_rewrite])

    return rewritten

def get_hints(selection):
    if not selection:
        return None
    
    hint_set = []
    for token, entities in selection.items():
        if token == "column":
            for term, cols in entities.items():
                hint = f" {term.capitalize()} refers to {' and '.join(cols)}."
                if hint not in hint_set:
                    hint_set.append(hint)
        
        elif token == "join":
            for term, joins in entities.items():
                hint = f" {term.capitalize()} refers to {' and '.join(joins)}."
                if hint not in hint_set:
                    hint_set.append(hint)
        
        elif token == "table":
            for term, tbls in entities.items():
                hint = f" {term.capitalize()} is stored in {' and '.join(tbls)} table."
                if hint not in hint_set:
                    hint_set.append(hint)
        
        elif token == "aggregate":
            for term, aggs in entities.items():
                hint = f" {term.capitalize()} refers to {' and '.join(aggs)}."
                if hint not in hint_set:
                    hint_set.append(hint)
        
        elif token == "output":
            if len(entities) == 0:
                continue
            if len(entities) == 1:
                hint = f" List {entities[0]}."
            else:
                cols = ", ".join(entities)
                hint = f" List {cols}."
            if hint not in hint_set:
                hint_set.append(hint)
                
    return "".join(hint_set) if hint_set else None
