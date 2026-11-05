"""Página «Retos SAMA»: trivias por niveles con la guardiana virtual.

Las preguntas salen de los documentos Word de `TRIVIA_DIR` (por defecto `docs/trivias/`);
ver `aula_sama.trivia` para el formato. Las respuestas no pasan por el RAG ni por Claude:
son las de los documentos revisados, así que la página funciona sin API keys.
"""

import re
from pathlib import Path

import streamlit as st

from aula_sama import trivia
from aula_sama.chat import persona
from aula_sama.config import get_settings

LETTERS = trivia.LETTERS
_ICONS = {"diapir": "🌋", "hidro": "🌊", "meteo": "⛅", "lodo": "🌋", "lluvia": "🌧️", "masa": "⛰️"}
# «La B.», «¡Mito! (La B).», «Las respuestas B y C son correctas.»: la letra ya se muestra aparte
_LEAD = re.compile(
    r"^(?P<word>¡\s*(?:Mito|Realidad)\s*!)?\s*\(?\s*(?:La|Las respuestas?)\s+[A-H](?:\s*(?:,|y|e)\s*[A-H])*"
    r"(?:\s+son correctas)?\s*\)?\s*\.?\s*"
)


@st.cache_data(show_spinner=False)
def _load(folder: str, stamp: tuple) -> tuple[list[trivia.Reto], dict[str, str]]:
    return trivia.load_retos(folder)


def load() -> tuple[list[trivia.Reto], dict[str, str]]:
    folder = Path(get_settings().trivia_dir)
    # La fecha de modificación entra en la clave: editar o agregar un .docx se refleja sin reiniciar
    stamp = tuple(sorted((p.name, p.stat().st_mtime) for p in folder.glob("*.docx"))) if folder.is_dir() else ()
    return _load(str(folder), stamp)


def icon(reto: trivia.Reto) -> str:
    key = f"{reto.slug} {reto.mission}".lower()
    return next((v for k, v in _ICONS.items() if k in key), "🧭")


def explanation(q: trivia.Question) -> str:
    m = _LEAD.match(q.explanation)
    if not m:
        return q.explanation
    rest = q.explanation[m.end() :]
    return f"{m.group('word')} {rest}" if m.group("word") else rest


# --- estado ------------------------------------------------------------------------------

def game() -> dict | None:
    return st.session_state.get("reto_game")


def start(slug: str, level: int = 0) -> None:
    st.session_state.reto_game = {"slug": slug, "level": level, "q": 0, "choice": None, "scores": {}, "phase": "question"}


def choose(i: int, correct: bool) -> None:
    g = game()
    g["choice"] = i
    g["scores"].setdefault(g["level"], 0)
    g["scores"][g["level"]] += int(correct)


def advance(reto: trivia.Reto) -> None:
    g = game()
    g["choice"] = None
    if g["q"] + 1 < len(reto.levels[g["level"]].questions):
        g["q"] += 1
    else:
        g["phase"] = "level_end"


def next_level(reto: trivia.Reto) -> None:
    g = game()
    if g["level"] + 1 < len(reto.levels):
        g.update(level=g["level"] + 1, q=0, choice=None, phase="question")
    else:
        g["phase"] = "done"


def leave() -> None:
    st.session_state.pop("reto_game", None)


# --- vistas ------------------------------------------------------------------------------

def guardian(expression: str, text: str) -> None:
    left, right = st.columns([1, 5], vertical_alignment="center")
    left.image(persona.avatar(expression), width=88)
    right.markdown(text)


def stars(n: int, total: int) -> str:
    return "⭐" * n + "☆" * (total - n)


def view_catalog(retos: list[trivia.Reto]) -> None:
    guardian(
        "neutral",
        "**¡Le doy la bienvenida al Reto SAMA!** Cada misión tiene tres niveles —Explorador, "
        "Investigador y Experto— con cinco preguntas. Después de cada respuesta le cuento por qué. "
        "¿Cuál quiere intentar?",
    )
    for reto in retos:
        with st.container(border=True):
            st.markdown(f"#### {icon(reto)} {reto.mission}")
            st.caption(" · ".join(f"Nivel {lv.number}: {lv.name}" for lv in reto.levels))
            cols = st.columns([2, *[1] * len(reto.levels)])
            cols[0].button("Empezar", key=f"start-{reto.slug}", type="primary", on_click=start, args=(reto.slug,),
                           width="stretch")
            for i, lv in enumerate(reto.levels[1:], start=1):
                cols[i].button(f"Nivel {lv.number}", key=f"lvl-{reto.slug}-{i}", on_click=start,
                               args=(reto.slug, i), width="stretch", help=f"Ir directo al nivel {lv.name}")


def view_question(reto: trivia.Reto, g: dict) -> None:
    lv = reto.levels[g["level"]]
    q = lv.questions[g["q"]]
    st.progress((g["q"] + (g["choice"] is not None)) / len(lv.questions),
                text=f"Nivel {lv.number} · {lv.name} — pregunta {g['q'] + 1} de {len(lv.questions)}")
    if g["q"] == 0 and g["choice"] is None and lv.intro:
        guardian("explicando", lv.intro)

    with st.container(border=True):
        st.markdown(f"##### {q.number}. {q.text}")
        answered = g["choice"] is not None
        for i, option in enumerate(q.options):
            label = f"**{LETTERS[i]}.** {option}"
            if answered:
                mark = "✅" if i in q.correct else ("❌" if i == g["choice"] else "▫️")
                st.markdown(f"{mark} {label}")
            else:
                st.button(label, key=f"opt-{g['level']}-{g['q']}-{i}", width="stretch",
                          on_click=choose, args=(i, i in q.correct))

    if answered:
        ok = g["choice"] in q.correct
        right = " y ".join(LETTERS[i] for i in q.correct)
        head = "**¡Muy bien!**" if ok else f"**Casi.** La respuesta es la **{right}**."
        if ok and len(q.correct) > 1:
            head += f" Aquí valen la **{right}**."
        guardian("explicando" if ok else "pensando", f"{head} {explanation(q)}")
        last = g["q"] + 1 == len(lv.questions)
        st.button("Ver resultado del nivel" if last else "Siguiente pregunta", type="primary",
                  on_click=advance, args=(reto,))


def view_level_end(reto: trivia.Reto, g: dict) -> None:
    lv = reto.levels[g["level"]]
    score, total = g["scores"].get(g["level"], 0), len(lv.questions)
    st.subheader(f"Nivel {lv.number} completado")
    st.markdown(f"### {stars(score, total)}  {score} de {total}")
    if lv.learning:
        st.info(f"**💡 Aprendizaje SAMA — Nivel {lv.number}**\n\n{lv.learning}")
    last = g["level"] + 1 == len(reto.levels)
    if not last:
        nxt = reto.levels[g["level"] + 1]
        st.button(f"Seguir al nivel {nxt.number}: {nxt.name}", type="primary", on_click=next_level, args=(reto,))
    else:
        st.button("Ver resultado final", type="primary", on_click=next_level, args=(reto,))


def view_done(reto: trivia.Reto, g: dict) -> None:
    played = [i for i in range(len(reto.levels)) if i in g["scores"]]
    score = sum(g["scores"].values())
    total = sum(len(reto.levels[i].questions) for i in played)
    ratio = score / total if total else 0
    if ratio >= 0.8:
        badge, msg = "🏅 Mirada experta", "¡Lee las señales del territorio como quien lo conoce a fondo!"
    elif ratio >= 0.5:
        badge, msg = "🔎 Investigación en camino", "Va muy bien. Repasar los aprendizajes le ayudará a subir de nivel."
    else:
        badge, msg = "🧭 Exploración", "Cada respuesta es un paso. Vale la pena intentarlo otra vez."
    if ratio >= 0.8:
        st.balloons()
    st.subheader(f"{icon(reto)} {reto.mission}")
    guardian("explicando", f"### {badge}\n**{score} de {total}** respuestas correctas. {msg}")
    for i in played:
        lv = reto.levels[i]
        st.markdown(f"- Nivel {lv.number} · {lv.name}: {stars(g['scores'][i], len(lv.questions))}")
    c1, c2 = st.columns(2)
    c1.button("Jugar otra vez", on_click=start, args=(reto.slug,), width="stretch")
    c2.button("Elegir otro reto", type="primary", on_click=leave, width="stretch")
    st.caption("¿Le quedaron dudas? Pregúntele a la guardiana en **Conversar**.")


# --- página ------------------------------------------------------------------------------

st.title("Reto SAMA")
st.caption("Un territorio, múltiples desafíos")

retos, errors = load()
by_slug = {r.slug: r for r in retos}

with st.sidebar:
    st.image(persona.avatar("neutral"), width=140)
    st.markdown("### Retos SAMA")
    st.caption("Ponga a prueba lo que sabe sobre las amenazas del territorio y cómo leer sus señales.")
    if game():
        st.button("Volver a los retos", on_click=leave, width="stretch")
    if errors:
        with st.expander(f"⚠️ {len(errors)} reto(s) no se pudieron leer"):
            for name, err in errors.items():
                st.caption(f"**{name}**: {err}")

g = game()
if g and g["slug"] not in by_slug:  # el documento desapareció o dejó de leerse
    leave()
    g = None

if not retos:
    st.warning(f"No hay retos disponibles en `{get_settings().trivia_dir}`.")
elif g is None:
    view_catalog(retos)
else:
    reto = by_slug[g["slug"]]
    {"question": view_question, "level_end": view_level_end, "done": view_done}[g["phase"]](reto, g)
