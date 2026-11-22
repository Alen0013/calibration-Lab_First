"""Build the LAB1 Markdown and Word reports from actual saved run artifacts.

Run from an environment containing python-docx:
    python scripts/build_report.py
The report never invents missing checks, author information or remote CI status.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


def read_json(path: Path, required: bool = False) -> dict:
    if not path.exists() and not required:
        return {}
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def decimal(value: float, places: int = 6) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def percent(value: float) -> str:
    return decimal(100 * value, 2) + " %"


def status(value: object) -> str:
    if value is True:
        return "Подтверждено"
    if value is False:
        return "Не пройдено"
    return "Нет сохранённого подтверждения"


def branch_names(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def plain(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", text)


class Report:
    """Small shared content model; both outputs receive the same measured facts."""

    def __init__(self, root: Path):
        self.root = root
        self.blocks: list[dict] = []

    def add(self, kind: str, **value) -> None:
        self.blocks.append({"kind": kind, **value})

    def heading(self, text: str, level: int = 1) -> None:
        self.add("heading", text=text, level=level)

    def p(self, text: str, small: bool = False) -> None:
        self.add("paragraph", text=text, small=small)

    def table(self, headers: list[str], rows: list[list], widths=None) -> None:
        self.add("table", headers=headers, rows=rows, widths=widths)

    def figure(self, name: str, caption: str, width=6.3, word=True) -> None:
        self.add("figure", name=name, caption=caption, width=width, word=word)

    def page(self) -> None:
        self.add("page")

    def write_markdown(self, path: Path) -> None:
        lines = []
        for block in self.blocks:
            kind = block["kind"]
            if kind == "heading":
                lines.append("#" * block["level"] + " " + block["text"])
            elif kind == "paragraph":
                lines.append(block["text"])
            elif kind == "table":
                lines.append("| " + " | ".join(block["headers"]) + " |")
                lines.append("| " + " | ".join(["---"] * len(block["headers"])) + " |")
                lines.extend(
                    "| " + " | ".join(str(value).replace("|", "/") for value in row) + " |"
                    for row in block["rows"]
                )
            elif kind == "figure":
                lines.append(f"![{block['caption']}](figures/{block['name']}.png)")
                lines.append(block["caption"])
            elif kind == "code":
                lines.append("```bash\n" + block["text"] + "\n```")
            elif kind == "list":
                lines.extend(f"{index}. {text}" for index, text in enumerate(block["items"], 1))
            elif kind == "link":
                lines.append(f"[{block['text']}]({block['url']})")
            if kind != "page":
                lines.append("")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _shade(cell, fill: str) -> None:
        node = OxmlElement("w:shd")
        node.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(node)

    @staticmethod
    def _hyperlink(paragraph, text: str, url: str) -> None:
        relationship = paragraph.part.relate_to(
            url,
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            is_external=True,
        )
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), relationship)
        run = OxmlElement("w:r")
        properties = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "174C78")
        properties.append(color)
        run.append(properties)
        node = OxmlElement("w:t")
        node.text = text
        run.append(node)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)

    @staticmethod
    def _table(document, block) -> None:
        table = document.add_table(rows=1, cols=len(block["headers"]))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        widths = block["widths"] or [6.85 / len(block["headers"])] * len(block["headers"])
        for column, width in zip(table.columns, widths, strict=True):
            column.width = Inches(width)
        for index, label in enumerate(block["headers"]):
            table.rows[0].cells[index].text = str(label)
        header = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(header)
        for values in block["rows"]:
            row = table.add_row()
            for cell, value in zip(row.cells, values, strict=True):
                cell.text = plain(str(value))
        borders = OxmlElement("w:tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = OxmlElement(f"w:{side}")
            for key, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
                border.set(qn(f"w:{key}"), value)
            borders.append(border)
        table._tbl.tblPr.append(borders)
        for row_index, row in enumerate(table.rows):
            no_split = OxmlElement("w:cantSplit")
            row._tr.get_or_add_trPr().append(no_split)
            for column_index, cell in enumerate(row.cells):
                cell.width = Inches(widths[column_index])
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                margins = OxmlElement("w:tcMar")
                for side, value in (
                    ("top", "65"),
                    ("bottom", "65"),
                    ("left", "90"),
                    ("right", "90"),
                ):
                    margin = OxmlElement(f"w:{side}")
                    margin.set(qn("w:w"), value)
                    margin.set(qn("w:type"), "dxa")
                    margins.append(margin)
                cell._tc.get_or_add_tcPr().append(margins)
                if row_index == 0:
                    Report._shade(cell, "E2EBF3")
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.line_spacing = 1.0
                    if column_index and len(str(cell.text)) < 25:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.font.size = Pt(10)
                        run.bold = row_index == 0
        document.add_paragraph().paragraph_format.space_after = Pt(0)

    def write_docx(self, path: Path) -> None:
        document = Document()
        section = document.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.left_margin, section.right_margin = Inches(0.85), Inches(0.8)
        section.top_margin, section.bottom_margin = Inches(0.65), Inches(0.65)
        section.footer_distance = Inches(0.28)
        for name in ("Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Caption"):
            style = document.styles[name]
            style.font.name = "Times New Roman"
            style.font.color.rgb = RGBColor(0, 0, 0)
            fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
            for attribute in list(fonts.attrib):
                if "theme" in attribute.lower():
                    del fonts.attrib[attribute]
            for alphabet in ("ascii", "hAnsi", "eastAsia", "cs"):
                fonts.set(qn(f"w:{alphabet}"), "Times New Roman")
            style.paragraph_format.space_after = Pt(6)
            style.paragraph_format.line_spacing = 1.12
        normal = document.styles["Normal"]
        normal.font.size = Pt(11.5)
        normal.paragraph_format.widow_control = True
        for name, size in (("Title", 21), ("Subtitle", 15), ("Heading 1", 15), ("Heading 2", 12)):
            document.styles[name].font.size = Pt(size)
            document.styles[name].paragraph_format.keep_with_next = True
            document.styles[name].paragraph_format.space_before = Pt(8)
        document.styles["Caption"].font.size = Pt(9.5)
        document.styles["Caption"].font.italic = False
        document.styles["Caption"].font.bold = False
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        field = OxmlElement("w:fldSimple")
        field.set(qn("w:instr"), "PAGE")
        footer._p.append(field)
        for block in self.blocks:
            kind = block["kind"]
            if kind == "heading":
                level = block["level"]
                style = "Title" if level == 1 else "Heading 1" if level == 2 else "Heading 2"
                document.add_paragraph(block["text"], style=style)
            elif kind == "paragraph":
                paragraph = document.add_paragraph(plain(block["text"]))
                if block.get("small"):
                    paragraph.paragraph_format.line_spacing = 1.0
                    paragraph.paragraph_format.space_after = Pt(4)
                    for run in paragraph.runs:
                        run.font.size = Pt(9.5)
            elif kind == "table":
                self._table(document, block)
            elif kind == "page":
                document.add_page_break()
            elif kind == "figure" and block["word"]:
                paragraph = document.add_paragraph()
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                paragraph.paragraph_format.keep_with_next = True
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.add_run().add_picture(
                    str(path.parent / "figures" / f"{block['name']}.png"),
                    width=Inches(block["width"]),
                )
                document.add_paragraph(block["caption"], style="Caption")
            elif kind == "code":
                paragraph = document.add_paragraph(block["text"])
                paragraph.paragraph_format.line_spacing = 1.0
                for run in paragraph.runs:
                    run.font.name = "Consolas"
                    run.font.size = Pt(9)
            elif kind == "list":
                for index, text in enumerate(block["items"], 1):
                    paragraph = document.add_paragraph(f"{index}. {plain(text)}")
                    paragraph.paragraph_format.space_after = Pt(4)
            elif kind == "link":
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.line_spacing = 1.0
                self._hyperlink(paragraph, block["text"], block["url"])
        document.core_properties.title = "Лабораторная работа 1 Калибровка вероятностей"
        document.core_properties.subject = "Вариант 25 Git окружение и разведочный анализ"
        document.core_properties.author = ""
        document.core_properties.comments = ""
        for node in list(document.styles.element.iter(qn("w:pBdr"))):
            node.getparent().remove(node)
        for node in list(document.element.iter(qn("w:pBdr"))):
            node.getparent().remove(node)
        document.save(path)


def build(root: Path) -> tuple[Path, Path]:
    output = root / "reports/LAB1"
    result = read_json(output / "results.json", required=True)
    repeat = read_json(output / "reproducibility.json")
    controls = read_json(output / "precommit_controls.json")
    tamper = read_json(output / "hash_tamper.json")
    checks = read_json(output / "local_checks.json")
    app_checks = read_json(output / "app_smoke.json")
    run_environment = read_json(output / "run_environment.json")
    remote = read_json(output / "github_status.json")
    summary, splits, metrics = result["summary"], result["splits"], result["metrics"]
    config = read_json(output / "config_snapshot.json", required=True)
    timestamp = datetime.fromisoformat(result["generated_at"]).astimezone(
        ZoneInfo("Asia/Yekaterinburg")
    )
    run_id, data = result["run_id"], result["data"]
    title_date = timestamp.strftime("%d.%m.%Y")
    base, calibrated, naive = (metrics[name] for name in ("uncalibrated", "calibrated", "naive"))
    brier_delta = calibrated["brier"] - base["brier"]
    brier_direction = (
        "увеличилась" if brier_delta > 0 else "уменьшилась" if brier_delta < 0 else "не изменилась"
    )
    ece_direction = (
        "выросла"
        if calibrated["ece"] > base["ece"]
        else "снизилась"
        if calibrated["ece"] < base["ece"]
        else "не изменилась"
    )
    report = Report(root)

    # Page 1: identity, purpose and data provenance.
    report.heading("Лабораторная работа 1")
    report.heading("Калибровка вероятностей классификатора", 2)
    author = result.get("author", "Автор не указан").strip()
    author_label = author if author.casefold() == "автор не указан" else f"Автор: {author}"
    report.p(f"Вариант {result['variant']} · дата {title_date} · {author_label}")
    report.p("Git окружение, прекоммитные проверки и разведочный анализ данных")
    report.p(f"Коммит кода: `{result['git_commit']}`", small=True)
    report.p(f"Основной прогон MLflow: `{run_id}`", small=True)
    report.p(f"SHA-256 сырых данных: `{data['sha256']}`", small=True)
    report.heading("Цель и полученный результат", 2)
    report.p(
        "Подготовлен локальный проект, который проверяет целостность данных, выполняет EDA, "
        "строит графики и демонстрирует калибровку вероятностей. Реальные негативные контроли "
        "и повторный анализ позволяют проверить результат. Удалённая публикация, защита "
        "главной ветки и GitHub Actions учитываются отдельно по подтверждённому состоянию."
    )
    report.p(
        f"В диагностическом эксперименте ошибка Brier после сигмоидальной калибровки {brier_direction}, "
        f"а ECE {ece_direction}. Поэтому результат не трактуется как гарантированное улучшение "
        "вероятностей. Расчёты относятся к фиксированному разделению данных этого запуска [E1–E3]."
    )
    report.heading("Паспорт данных", 2)
    report.table(
        ["Характеристика", "Значение"],
        [
            ["Название", "Breast Cancer Wisconsin Diagnostic"],
            ["Источник", "UCI; числовое представление scikit-learn [S1, S2]"],
            ["Лицензия набора", data["license"]],
            ["Наблюдения и признаки", f"{summary['rows']} × {summary['features']}"],
            ["Сырой файл", data["path"]],
            ["Размер файла", f"{data['size_bytes']} байт"],
            ["Верификация содержимого", data["status"]],
        ],
        [2.15, 4.7],
    )
    report.p(
        "Источник значений — results.json и hash_manifest.json [E1]. Метод получения: "
        f"`{data.get('acquisition', 'не указан')}`. Адрес закреплённого файла указан в манифесте. "
        "Проверка сравнивает размер и SHA-256 с зафиксированным эталоном и не обновляет его при отказе.",
        small=True,
    )
    report.p(
        "ИИ использован при разработке кода, проверок и черновиков документации. "
        "Экспериментальные числа получены исполнением кода. Подробное раскрытие содержится "
        "в defense_checklist.md; рецензия БЯМ носит совещательный характер.",
        small=True,
    )

    # Page 2: dataset quality and classes.
    report.page()
    report.heading("Структура и качество данных", 2)
    report.p(
        "Признаки описывают форму и текстуру ядер клеток. Для основных измерений представлены "
        "средние значения, стандартные ошибки и наибольшие значения. Все признаки числовые. "
        "Идентификатор исходной записи UCI не используется как предиктор [S1, S2]. "
        "В scikit-learn метка malignant имеет код 0; в эксперименте она явно преобразована "
        "в положительный класс y=1. Поэтому вероятности на диаграмме относятся к malignant [E3]."
    )
    report.table(
        ["Показатель", "Результат"],
        [
            ["Пропуски", summary["missing_values"]],
            ["Полные дубликаты строк", summary["duplicate_rows"]],
            ["Отрицательные значения признаков", summary["negative_values"]],
            ["Неконечные значения", summary["nonfinite_values"]],
            ["Нулевые значения признаков", summary["zero_values"]],
            ["Ячейки за границами IQR", summary["outlier_cells"]],
            ["Строки хотя бы с одним значением за границами", summary["outlier_rows"]],
        ],
        [4.9, 1.95],
    )
    report.p(
        "Источник таблицы — eda_stats.csv, раздел dataset; расчёт summarize_data [E2, E3].",
        small=True,
    )
    report.figure(
        "class_balance",
        f"Рисунок 1. Доли классов: benign — {summary['class_counts']['benign']} "
        f"({percent(summary['class_shares']['benign'])}), malignant — {summary['class_counts']['malignant']} "
        f"({percent(summary['class_shares']['malignant'])}). Источник — class_balance.csv, прогон [E1].",
        width=6.0,
    )
    report.p(
        "Состав выборки учитывается стратификацией при разбиении. Отсутствие пропусков не требует "
        "импутации. Нулевое измерение само по себе не считается ошибкой; значения в EDA не удаляются."
    )

    # Page 3: distributions, correlations and potential outliers.
    report.page()
    report.heading("Распределения и связи признаков", 2)
    report.p(
        "Распределения mean radius, mean texture, mean area и mean concavity построены отдельно "
        "по классам. Размерные и безразмерные признаки имеют разные масштабы; перед логистической "
        "регрессией используется StandardScaler, обучаемый только на обучающей части. Гистограммы "
        "сохранены в figures/distributions.png, полные описательные статистики — в feature_summary.csv [E3]."
    )
    report.figure(
        "distributions",
        "Дополнительный график. Распределения ключевых признаков по классам. "
        "Средние и объём подписаны на изображении; источник — feature_summary.csv [E3].",
        word=False,
    )
    report.figure(
        "correlations",
        f"Рисунок 2. Корреляции Пирсона средних признаков. Наибольшее абсолютное значение "
        f"по всем парам: {decimal(summary['maximum_abs_correlation'])}; пара "
        f"{summary['correlation_pair'][0]} и {summary['correlation_pair'][1]}. "
        "Источник — correlations.csv и results.json [E1, E3].",
        width=5.85,
    )
    report.p(
        f"Потенциальные выбросы определены как значения ниже Q1 − {decimal(config['iqr_multiplier'], 1)} × IQR "
        f"или выше Q3 + {decimal(config['iqr_multiplier'], 1)} × IQR. Найденные крайние значения "
        "сохраняются: это статистическая отметка, а не доказательство ошибки. Число ячеек и строк "
        "различается, поскольку в одной строке может быть несколько таких значений. "
        "Диаграмма по признакам сохранена в figures/outliers.png [E2, E3]."
    )
    report.figure(
        "outliers",
        "Дополнительный график. Признаки с наибольшим числом значений за межквартильными границами. "
        "Источник — feature_summary.csv; наблюдения не удалялись [E3].",
        word=False,
    )

    # Page 4: exact experimental design and metric results.
    report.page()
    report.heading("План разбиения и эксперимент", 2)
    report.p(
        f"Разбиение стратифицированное, зерно случайности — {result['seed']}. "
        "Индексы частей записаны в split_indices.json и не пересекаются. "
        "Доли заданы до оценки теста в configs/lab1.yaml; гиперпараметры по тестовым значениям не выбирались [E3, E4]."
    )
    report.table(
        ["Часть", "Объём", "Benign", "Malignant", "Назначение"],
        [
            [
                "Обучение",
                splits["train"],
                splits["class_counts"]["train"]["benign"],
                splits["class_counts"]["train"]["malignant"],
                "Scaler и LogReg",
            ],
            [
                "Калибровка",
                splits["calibration"],
                splits["class_counts"]["calibration"]["benign"],
                splits["class_counts"]["calibration"]["malignant"],
                "Sigmoid",
            ],
            [
                "Тест",
                splits["test"],
                splits["class_counts"]["test"]["benign"],
                splits["class_counts"]["test"]["malignant"],
                "Итоговая оценка",
            ],
        ],
        [1.4, 0.75, 0.95, 1.05, 2.7],
    )
    report.p("Источник — results.json, раздел splits, и split_indices.json [E1, E3].", small=True)
    report.p(
        "Базовая модель — Pipeline из StandardScaler и LogisticRegression. "
        f"Параметр C равен {decimal(config['logistic_c'], 1)}, max_iter — {config['max_iter']}. "
        "Затем CalibratedClassifierCV с методом sigmoid получает FrozenEstimator обученной модели "
        "и независимую калибровочную часть. Коэффициенты базовой модели не переобучаются [E3, E4, S3]."
    )
    report.p(
        "Наивный ориентир выдаёт всем объектам частоту malignant из обучающей части. "
        "На тесте сопоставляются одинаковые строки для всех методов. Accuracy вычисляется "
        "после порога вероятности 0,5; ROC-AUC характеризует ранжирование [E3]."
    )
    report.table(
        ["Метод", "Brier", "Log loss", "ECE", "Accuracy", "ROC-AUC"],
        [
            [
                label,
                *[
                    decimal(metrics[key][metric], 5)
                    for metric in ("brier", "log_loss", "ece", "accuracy", "roc_auc")
                ],
            ]
            for key, label in (
                ("uncalibrated", "LogReg"),
                ("calibrated", "LogReg + sigmoid"),
                ("naive", "Постоянная вероятность"),
            )
        ],
        [1.7, 1.03, 1.03, 1.03, 1.03, 1.03],
    )
    report.p(
        "Источник — calibration_metrics.csv; метрики без округления — results.json; прогон [E1].",
        small=True,
    )
    report.p(
        "Brier — средний квадрат ошибки вероятности; log loss штрафует уверенные неверные ответы. "
        "Обе величины зависят не только от калибровки, но и от разделимости классов. "
        "Их нельзя интерпретировать как изолированную меру калибровки [S4]."
    )
    report.p(
        f"ECE рассчитана по {result['method']['bins']} равным интервалам вероятности: "
        "суммируются абсолютные отклонения доли исходов от средней вероятности, взвешенные "
        "долей объектов интервала. Левая граница включается; последний интервал также включает "
        "вероятность 1. Пустой интервал имеет нулевой вес. Численности и вклады сохранены "
        "в calibration_bins.csv [E3, E4]."
    )

    # Page 5: problem plot and honest interpretation.
    report.page()
    report.heading("График проблемы варианта", 2)
    report.figure(
        "calibration",
        f"Рисунок 3. Диаграмма калибровки на {splits['test']} тестовых наблюдениях. "
        f"Использовано {result['method']['bins']} равных интервалов; числа у точек — размеры групп. "
        "Нижняя панель показывает распределение вероятностей. Источник — calibration_bins.csv [E1, E3].",
        width=6.2,
    )
    report.p(
        f"После калибровки Brier {brier_direction}: {decimal(base['brier'])} → "
        f"{decimal(calibrated['brier'])}. ECE {ece_direction}: {decimal(base['ece'])} → "
        f"{decimal(calibrated['ece'])}. Accuracy составила {percent(base['accuracy'])} "
        f"и {percent(calibrated['accuracy'])} соответственно. Это разные свойства прогноза: "
        "изменение меток при пороге не доказывает улучшения вероятностей [E1]."
    )
    report.p(
        f"У постоянного ориентира ECE равна {decimal(naive['ece'])}, при этом ROC-AUC "
        f"равна {decimal(naive['roc_auc'], 1)}. Близость общей частоты классов позволяет "
        "получить малую ECE без различения отдельных объектов. Поэтому анализ использует "
        "диаграмму, вероятностные ошибки и ранжирование вместе [E1, E3]."
    )
    report.p(
        "Малые группы на диаграмме дают неустойчивые оценки. Этот запуск иллюстрирует "
        "проблему варианта, но не устанавливает универсальное превосходство метода. "
        "Логистическая регрессия уже может давать хорошо согласованные вероятности; "
        "дополнительная калибровка не гарантирует улучшение на конкретном тесте [S4]."
    )

    # Page 6: actual control evidence, repeat and remaining remote work.
    report.page()
    report.heading("Git и негативные контроли", 2)
    names = branch_names(root)
    report.p(
        "Локальные ветки на момент сборки отчёта: "
        + (", ".join(f"`{name}`" for name in names) or "нет доступного списка")
        + "."
    )
    report.p(
        "Подготовлены .gitignore, .gitattributes, шаблон PR и workflow с проверкой quality. "
        "Работа интегрируется в develop; main содержит начальную основу до принятия изменений "
        "через PR. Pre-commit запускает Ruff, проверку индекса на известные шаблоны секретов, "
        "ограничение размера и быстрые проверки синтаксиса. Локальный хук дополняется серверным CI [E5, S5]."
    )
    labels = {
        "style": "Нарушение стиля",
        "secret": "Искусственный секрет",
        "oversize": "Большой бинарный файл",
    }
    cases = controls.get("cases", [])
    report.table(
        ["Контроль", "Код git commit", "HEAD прежний", "Блокировка"],
        [
            [
                labels.get(case["case"], case["case"]),
                case["commit_exit_code"],
                "Да" if case["head_unchanged"] else "Нет",
                status(case["blocked"]),
            ]
            for case in cases
        ]
        or [["См. журнал", "Нет данных", "Нет данных", "Не подтверждено"]],
        [2.45, 1.2, 1.2, 2.0],
    )
    report.p(
        "Источник — precommit_controls.json и precommit_block.log; scripts/negative_controls.py "
        "создаёт временный репозиторий и выполняет настоящие команды commit. "
        "Действующие настройки скопированы в precommit_config.yaml [E5].",
        small=True,
    )
    report.p(
        "Подмена данных: " + status(tamper.get("rejected")) + ". "
        "Изменяется байт временной копии, затем вызывается тот же валидатор хеша. "
        "Неизменность оригинала: " + status(tamper.get("original_unchanged")) + ". "
        "Ожидаемый и полученный хеши сохранены в hash_tamper.json и hash_tamper.log [E7]."
    )
    report.heading("Воспроизводимость", 2)
    report.p(
        f"Результат повторения: {repeat.get('status', 'нет сохранённого подтверждения')}. "
        "Критерий — точное совпадение в записанном окружении при одном потоке BLAS [E6]."
    )
    report.p(f"Первый повторный прогон: `{repeat.get('first_run_id', 'не указан')}`", small=True)
    report.p(f"Второй повторный прогон: `{repeat.get('second_run_id', 'не указан')}`", small=True)
    if repeat:
        equal_count = sum(value is True for value in repeat.get("files_equal", {}).values())
        report.p(
            f"Побайтово совпали {equal_count} из {len(repeat.get('files_equal', {}))} "
            f"детерминированных файлов. Наибольшая абсолютная разность метрик — "
            f"{decimal(repeat.get('max_absolute_difference', 0), 12)}. Время, run_id и пути вывода "
            "не сравниваются как результаты анализа. Хеши файлов и разности сохранены в reproducibility.json [E6]."
        )
    passed = checks.get("tests", {}).get("passed")
    report.p(
        (
            f"Успешных автоматических тестов: {passed}. "
            if passed is not None
            else "Количество успешных тестов: см. фактический журнал проверки. "
        )
        + "Локальный pre-commit: "
        + status(checks.get("commands", {}).get("precommit", {}).get("pass"))
        + ". "
        "Источник — local_checks.json и журналы локальной проверки [E8]."
    )
    report.p(
        "Удалённый репозиторий: " + status(remote.get("published")) + "; "
        "GitHub Actions: " + status(remote.get("ci_passed")) + "; "
        "защита main: " + status(remote.get("main_protected")) + ". "
        "До их подтверждения удалённая часть задания остаётся незавершённой. "
        "Конфигурация .github/branch_protection.json сама по себе защиту не включает [S6]."
    )

    # Page 7: conclusions, reproducible entry points and evidence map.
    report.page()
    report.heading("Выводы", 2)
    conclusions = [
        f"Проанализировано {summary['rows']} наблюдений, для которых сохранены сводки и паспорт данных [E2].",
        f"Набор содержит {summary['features']} числовых признаков с различными масштабами [E2].",
        f"Число пропусков равно {summary['missing_values']}; заполнение отсутствующих значений не потребовалось [E2].",
        f"Доля malignant составляет {percent(summary['class_shares']['malignant'])}; состав классов учтён стратификацией [E2, E3].",
        f"Кандидаты в выбросы встречаются в {summary['outlier_rows']} строках; автоматическое удаление не применялось [E2].",
        f"Наибольшая абсолютная корреляция признаков равна {decimal(summary['maximum_abs_correlation'])}; присутствуют тесные линейные связи [E1, E3].",
        f"Изменение Brier после sigmoid составило {decimal(brier_delta)}; "
        + (
            "на выбранном тесте вероятностная ошибка выросла [E1]."
            if brier_delta > 0
            else "это фактический результат выбранного теста [E1]."
        ),
    ]
    if repeat:
        conclusions.append(
            f"При повторении совпали {sum(value is True for value in repeat.get('files_equal', {}).values())} "
            "детерминированных артефактов; основной результат воспроизводится в записанном окружении [E6]."
        )
    if cases:
        conclusions.append(
            f"Заблокировано {sum(case.get('blocked') is True for case in cases)} ошибочных коммита; журнал показывает причины отказов [E5]."
        )
    report.add("list", items=conclusions)
    report.heading("Запуск и источники результатов", 2)
    report.p(
        "Установка и запуск приложения описаны в README.md. Основные команды проверки "
        "в активированном окружении выполняются из корня проекта:"
    )
    report.add(
        "code",
        text="python -m calibration_lab.cli verify\npython -m calibration_lab.cli eda\npython -m calibration_lab.cli tamper\npython scripts/negative_controls.py\npython -m calibration_lab.cli reproduce",
    )
    report.p(
        "Версии основного прогона: "
        + "; ".join(f"{name} {version}" for name, version in result["environment"].items())
        + ". Расширенный снимок среды снят после запуска: requirements-run.txt и run_environment.json [E1, E9].",
        small=True,
    )
    report.p(
        "requirements-lock.txt относится к независимой чистой установке. Этот снимок отличается "
        "от среды основного анализа, в частности версиями SciPy, joblib и threadpoolctl. "
        "Успешная установка и тесты подтверждают работоспособность окружения, но не численное "
        "совпадение результатов между средами. Точная воспроизводимость проверена только "
        "между повторами в одной записанной среде [E6, E8, E9].",
        small=True,
    )
    if run_environment:
        main_dependencies = run_environment.get("versions", {})
        clean_dependencies = checks.get("dependencies", {})
        environment_rows = [
            [
                name,
                main_dependencies.get(name, "см. снимок"),
                clean_dependencies.get(name, "см. снимок"),
            ]
            for name in ("scipy", "joblib", "threadpoolctl")
        ]
        report.table(
            ["Пакет", "Основной прогон", "Чистая установка"],
            environment_rows,
            [2.1, 2.35, 2.4],
        )
    report.p(
        "E1 — results.json, основной MLflow run_id указан в шапке. E2 — eda_stats.csv. "
        "E3 — src/calibration_lab/analysis.py и создаваемые CSV. E4 — configs/lab1.yaml и config_snapshot.json. "
        "E5 — scripts/negative_controls.py, precommit_controls.json, precommit_block.log. "
        "E6 — reproducibility.json и reproducibility.log, команда reproduce. "
        "E7 — src/calibration_lab/data.py, hash_tamper.json и hash_tamper.log. "
        "E8 — local_checks.json, app_smoke.json и журналы проверки. Пути JSON, CSV и журналов, если не указано иное, "
        "относительны reports/LAB1/. E9 — run_environment.json и requirements-run.txt; "
        "requirements-lock.txt описывает другую, чистую среду установки.",
        small=True,
    )

    # Page 8: references, supplied artifacts and explicit remaining steps.
    report.page()
    report.heading("Источники и комплект сдачи", 2)
    report.p(
        "Требования — методичка labs_new (1)(1).docx, разделы общих сведений, карточки варианта, "
        "общих артефактов и лабораторной работы. Официальные технические источники приведены ниже; "
        "в Word их названия являются ссылками."
    )
    sources = [
        (
            "S1 UCI Breast Cancer Wisconsin Diagnostic и лицензия CC BY 4.0",
            "https://archive.ics.uci.edu/dataset/17/breast+cancer+wisconsin+diagnostic",
        ),
        (
            "S2 scikit-learn 1.8 load_breast_cancer",
            "https://scikit-learn.org/1.8/modules/generated/sklearn.datasets.load_breast_cancer.html",
        ),
        (
            "S3 scikit-learn 1.8 CalibratedClassifierCV",
            "https://scikit-learn.org/1.8/modules/generated/sklearn.calibration.CalibratedClassifierCV.html",
        ),
        (
            "S4 scikit-learn 1.8 Probability calibration",
            "https://scikit-learn.org/1.8/modules/calibration.html",
        ),
        ("S5 pre-commit Официальная документация", "https://pre-commit.com/"),
        (
            "S6 GitHub Защищённые ветки",
            "https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches",
        ),
        ("S7 MLflow Tracking", "https://mlflow.org/docs/latest/ml/tracking/"),
    ]
    for label, url in sources:
        report.add("link", text=label, url=url)
    report.p(
        "Атрибуция данных: Wolberg, W., Mangasarian, O., Street, N., & Street, W. "
        "Breast Cancer Wisconsin Diagnostic. UCI Machine Learning Repository. "
        "DOI: 10.24432/C5DW2B. Сведения об изменении представления и перекодировании целевого класса "
        "сохранены в манифесте и методе эксперимента."
    )
    report.heading("Основные материалы", 2)
    report.table(
        ["Материал", "Расположение"],
        [
            ["Отчёт и сводка", "lab1_report.md, lab1_report.docx, eda_stats.csv"],
            ["Хеши и EDA", "hash_manifest.json, results.json, figures/"],
            ["Блокировки коммитов", "precommit_config.yaml, precommit_block.log"],
            ["Подмена и повторение", "hash_tamper.*, reproducibility.*"],
            ["Подготовка к защите", "docs/defense_notes.md, docs/video_script.md"],
            ["Раскрытие ИИ", "defense_checklist.md"],
        ],
        [2.0, 4.85],
    )
    report.p(
        "Локальное приложение показывает данные, графики, показатели калибровки и результаты "
        "проверок. Сценарий демонстрации включает фактический запуск проверок и связь с отчётом. "
        "Требуемая методичкой продолжительность скринкаста — 3–5 минут; видео и рецензия БЯМ "
        "передаются вместе с архивом проекта."
    )
    if app_checks:
        report.p(
            f"HTTP-проверки работающего процесса приложения: {app_checks.get('check_count', 'не указано')}; "
            f"успешность набора: {status(app_checks.get('all_passed'))}. Проверены ответы сервера "
            "и выполнение команды verify через API. Это не подтверждение визуальной работы "
            "браузера: app_smoke.json явно фиксирует browser_used=false [E8]."
        )
    report.p(
        "Подготовленная видеодемонстрация CLI воспроизводит реальные сохранённые выводы команд "
        "и графики. Она не является записью браузерного окна. Для скринкаста графического "
        "приложения предусмотрены сценарий и скрипт записи, запускаемый на компьютере обучающегося."
    )
    report.heading("Действия перед окончательной сдачей", 2)
    report.p(
        "Нужно указать реальные сведения об авторе, подтвердить публикацию и защиту main, "
        "приложить успешный серверный запуск CI и оформить необходимое ревью. Обучающийся "
        "проверяет демонстрацию и составляет собственный критический комментарий к рецензии. "
        "Эти действия не подменяются локальным конфигурационным файлом или подготовленным шаблоном."
    )
    markdown, word = output / "lab1_report.md", output / "lab1_report.docx"
    report.write_markdown(markdown)
    report.write_docx(word)
    return markdown, word


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    for path in build(args.root.resolve()):
        print(path)


if __name__ == "__main__":
    main()
