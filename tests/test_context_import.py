from core.context_import import (CONTEXT_REQUEST, CONTEXT_END, CONTEXT_START, merged_glossary,
                                 merged_prompt, parse_context)

ANSWER = """```markdown
OBRA: El Rey Champiñón Crece Hacia Arriba

## CONTEXTO
Lin Feng renace como un champiñón en el bosque de Tianyun.
Su aliada es la espíritu Xiao Ling.

## **ESTILO**
- Xiao Ling trata de tú a Lin Feng.
- Las técnicas van en mayúsculas.

## GLOSARIO
original | traducción | categoría | nota
--- | --- | --- | ---
林枫 | Lin Feng | personaje | protagonista
天云森林 | Bosque Tianyun | lugar |
小灵 | Xiao Ling | personaje | espíritu aliada
```"""


def test_chatgpt_answer_becomes_glossary_and_instructions():
    imported = parse_context(ANSWER)
    assert imported.title == "El Rey Champiñón Crece Hacia Arriba"
    assert "renace como un champiñón" in imported.context
    assert "Las técnicas van en mayúsculas." in imported.style
    assert [(term["source"], term["target"], term["category"]) for term in imported.glossary] == [
        ("林枫", "Lin Feng", "personaje"), ("天云森林", "Bosque Tianyun", "lugar"), ("小灵", "Xiao Ling", "personaje"),
    ]
    assert imported.glossary[0]["note"] == "protagonista"


def test_reimporting_replaces_only_the_imported_block():
    prompt = merged_prompt("Mantén los insultos suaves.", parse_context(ANSWER))
    assert prompt.startswith("Mantén los insultos suaves.") and CONTEXT_START in prompt
    updated = merged_prompt(prompt, parse_context("## CONTEXTO\nNuevo arco en la capital."))
    assert updated.count(CONTEXT_START) == 1 and updated.count(CONTEXT_END) == 1
    assert "Nuevo arco" in updated and "Bosque" not in updated and "insultos suaves" in updated


def test_imported_terms_win_and_old_ones_stay():
    existing = "林枫\tLin Fen\tpersonaje\n血刀\tEspada de Sangre\tobjeto"
    combined = merged_glossary(existing, parse_context(ANSWER))
    targets = {term["source"]: term["target"] for term in combined}
    assert targets["林枫"] == "Lin Feng" and targets["血刀"] == "Espada de Sangre"


def test_other_shapes_are_accepted():
    loose = parse_context("**Contexto:** Un cultivador vuelve al pasado.\n**Glosario:**\n- 陈凡 → Chen Fan")
    assert loose.context == "Un cultivador vuelve al pasado."
    assert loose.glossary[0]["target"] == "Chen Fan"
    as_json = parse_context('{"obra": "X", "contexto": "Y", "glosario": [{"original": "甲", "traducción": "Jia"}]}')
    assert as_json.title == "X" and as_json.glossary[0]["source"] == "甲"
    bare = parse_context("林枫 = Lin Feng\n小灵 = Xiao Ling")
    assert len(bare.glossary) == 2 and not bare.context
    assert "## GLOSARIO" in CONTEXT_REQUEST


def test_dialog_saves_into_the_project():
    import os
    import pytest
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from ui.context_import_dialog import ContextImportDialog
    app = QApplication.instance() or QApplication([])

    class Manager:
        saved = None

        def translation_profile(self, project_type, project):
            return {"prompt": "Instrucciones propias.", "glossary": [{"source": "林枫", "target": "Lin Fen", "category": "personaje", "note": ""}]}

        def set_translation_profile(self, project_type, project, prompt, glossary):
            self.saved = (prompt, glossary)

    manager = Manager()
    dialog = ContextImportDialog(manager, "Manhua", "El Rey Champiñón", None)
    assert not dialog.import_button.isEnabled()
    dialog.answer.setPlainText(ANSWER)
    assert dialog.import_button.isEnabled()
    assert "3 término(s): 2 nuevo(s), 1 con otra traducción" in dialog.summary.text()
    dialog.accept()
    prompt, glossary = manager.saved
    assert prompt.startswith("Instrucciones propias.") and "Bosque" not in prompt and "champiñón" in prompt
    assert glossary[0]["target"] == "Lin Feng" and dialog.imported_terms == 3
    dialog.close(); app.processEvents()


def test_a_rule_with_a_colon_does_not_open_another_section():
    imported = parse_context(
        "## ESTILO\nNombres: los nombres chinos se dejan en pinyin.\nLas técnicas van en mayúsculas.\n\n"
        "## GLOSARIO\nRules | Reglas\n林枫 | Lin Feng\nesto no es un término\n"
    )
    assert "pinyin" in imported.style and "mayúsculas" in imported.style
    assert [term["source"] for term in imported.glossary] == ["Rules", "林枫"]
