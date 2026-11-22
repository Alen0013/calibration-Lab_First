# Источники

Дата проверки ссылок — 22 ноября 2026 года. Требования к составу лабораторной взяты из приложенной пользователем методички `labs_new (1)(1).docx`. Для технических решений использованы первичные источники ниже.

| Источник | Адрес | Что подтверждает |
| --- | --- | --- |
| UCI Machine Learning Repository | https://archive.ics.uci.edu/dataset/17/breast+cancer+wisconsin+diagnostic | Набор Breast Cancer Wisconsin Diagnostic, описание признаков, авторы, лицензия CC BY 4.0 |
| DOI набора | https://doi.org/10.24432/C5DW2B | Постоянный идентификатор источника данных |
| scikit-learn 1.8, загрузчик | https://scikit-learn.org/1.8/modules/generated/sklearn.datasets.load_breast_cancer.html | Представление набора в scikit-learn, признаки и целевые метки |
| scikit-learn 1.8, калибровка | https://scikit-learn.org/1.8/modules/calibration.html | Смысл вероятностной калибровки, диаграмма надёжности, ограничения Brier score и log loss |
| scikit-learn 1.8, CalibratedClassifierCV | https://scikit-learn.org/1.8/modules/generated/sklearn.calibration.CalibratedClassifierCV.html | Сигмоидальная калибровка уже обученной модели через FrozenEstimator; непересекающиеся данные обучения и калибровки |
| scikit-learn 1.8, примеры FrozenEstimator | https://scikit-learn.org/1.8/auto_examples/frozen/plot_frozen_examples.html | Практическое применение замороженной модели |
| pre-commit | https://pre-commit.com/ | Установка Git hook и выполнение проверок перед коммитом |
| GitHub, защищённые ветки | https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches | Pull request, обязательные проверки, запрет force push и ограничения доступности защиты |
| MLflow Tracking | https://mlflow.org/docs/latest/ml/tracking/ | Регистрация параметров, метрик и артефактов с идентификаторами прогонов |

## Атрибуция данных

Wolberg, W., Mangasarian, O., Street, N., & Street, W. (1993). *Breast Cancer Wisconsin (Diagnostic)* [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5DW2B.

Набор распространяется UCI по лицензии Creative Commons Attribution 4.0 International. В проекте используются числовые данные в представлении scikit-learn. Способ получения из официального версионного источника или установленного пакета, сведения о происхождении и точные хеши содержит манифест проекта. Представление scikit-learn не содержит столбца идентификатора пациента из исходного файла UCI. Перекодирование целевого класса в модели описывается в отчёте и в коде.

## Как читать результаты

График калибровки сопоставляет среднюю предсказанную вероятность с долей положительных исходов в группе наблюдений. Brier score и log loss оценивают качество вероятностного прогноза в целом; изменение этих метрик не следует выдавать за изолированное измерение калибровки. ECE, определённая в коде проекта, зависит от способа разбиения вероятностей на интервалы. Малое число наблюдений в интервале делает оценку доли неустойчивой.

Числа конкретного запуска берутся из результатов проекта, а не из внешней документации. Внешние источники объясняют метод, но не подтверждают локально полученные метрики.
