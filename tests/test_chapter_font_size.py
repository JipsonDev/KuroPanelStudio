from core.chapter_font_size import chapter_font_size, unify_chapter_font_size


def balloon(fitted, requested=40, text="Hola", **style):
    return {"text": text, "style": {"font_size": requested, "auto_fit": True, **style},
            "balloon_layout_snapshot": {"font_size": fitted} if fitted else None}


def test_dialogue_is_capped_at_the_chapter_size():
    pages = {
        "1": [balloon(28), balloon(36), balloon(24, requested=30)],
        "2": [balloon(30), balloon(None), balloon(60, sfx_enabled=True), balloon(26, text=" ")],
    }
    assert chapter_font_size(pages) == 29
    changed = unify_chapter_font_size(pages, 28)
    sizes = [region["style"]["font_size"] for region in pages["1"] + pages["2"]]
    assert sizes == [28, 28, 28, 28, 28, 40, 40]
    assert changed == 5
    assert pages["1"][0].get("balloon_layout_snapshot") is None  # refitted at the new size
    # Unifying again changes nothing; a later, larger chapter size raises them again.
    assert unify_chapter_font_size(pages, 28) == 0
    unify_chapter_font_size(pages, 32)
    assert pages["1"][1]["style"]["font_size"] == 32


def test_too_few_fitted_balloons_give_no_size():
    assert chapter_font_size({"1": [balloon(30), balloon(None)]}) is None


def test_spanish_lines_do_not_end_on_articles_or_prepositions():
    from core.linguistic_composer import linguistic_wrap
    measure = lambda value: len(value) * 10.0
    lines = linguistic_wrap("NO PUEDO CREER QUE ESTÉS AQUÍ DE NUEVO CON LA ESPADA", [180, 240, 240, 180], measure, "es")
    assert not any(line.split()[-1] in {"DE", "CON", "LA", "QUE"} for line in lines[:-1])
    lines = linguistic_wrap("ESPERA. ¿DE VERDAD VAS A IR SOLO A LA MONTAÑA DEL NORTE?", [150, 220, 240, 220, 150], measure, "es")
    assert lines[0] == "ESPERA."


def test_chapter_size_is_measured_in_the_balloons(monkeypatch):
    from PIL import Image, ImageDraw, ImageFont
    import core.export_manager as export
    # Scalable font everywhere (the CI images may lack Windows fonts).
    monkeypatch.setattr(export, "_font", lambda size, *_args: ImageFont.load_default(size=max(1, size)))
    from core.chapter_font_size import measure_chapter_font_size
    image = Image.new("RGB", (900, 1600), (90, 100, 110))
    draw = ImageDraw.Draw(image)
    regions = []
    texts = ["HOLA, ¿CÓMO ESTÁS?", "NO PUEDO CREER QUE ESTÉS AQUÍ DE NUEVO", "¡CORRE!",
             "ESA ESPADA ES DEMASIADO PESADA PARA TI"]
    for index, text in enumerate(texts):
        top = 50 + index * 380
        draw.ellipse((200, top, 680, top + 330), fill="white", outline="black", width=4)
        regions.append({"x": 290, "y": top + 90, "width": 300, "height": 150, "applied_text": text,
                        "text": text, "style": {"font_size": 150, "auto_fit": True, "balloon_fit": True}})
    size = measure_chapter_font_size([("page", regions)], lambda _page: image)
    assert size is not None and 6 < size < 150
    # A capped box is measured from the size chosen before unifying.
    unify_chapter_font_size({"page": regions}, 12)
    assert regions[0]["style"]["font_size"] == 12
    assert measure_chapter_font_size([("page", regions)], lambda _page: image) == size
