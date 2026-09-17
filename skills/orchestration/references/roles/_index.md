# Каталог ролей (библиотека промтов)

Иерархия оркестратора: **задача → домен → роль → шаблон роли → самодостаточный
промт исполнителя**. Не импровизируй промт с нуля. Роли нет — ФАБРИКА РОЛИ:
напиши по `_template.md`, добавь в `_index.md`, используй. Роль остаётся навсегда.

## Бизнес и стратегия (business/)
| Роль | Файл | Когда |
|---|---|---|
| Бизнес-аналитик | business/бизнес-аналитик.md | as-is/to-be/gap-анализ, требования |
| Консультант (McKinsey) | business/консультант-mckinsey-frameworks.md | структурированные стратегии, issue trees |
| Стратег | business/стратег.md | выбор направления, позиционирование |
| Конкурентный аналитик | business/конкурентный-аналитик.md | сравнение конкурентов |
| Product Manager | business/product-manager.md | roadmap, приоритизация фич |

## Финансы (finance/)
| Роль | Файл | Когда |
|---|---|---|
| CFO / Фин. директор | finance/cfo-финсовый-директор.md | фин. стратегия, бюджет |
| Финансовый аналитик | finance/финансовый-аналитик.md | моделирование, оценка, DCF |
| Венчурный инвестор | finance/венчурный-инвестор.md | due diligence, term sheets |
| Unit Economics | finance/unit-economics-analyst.md | CAC/LTV, payback, margins |
| Макроэкономист | finance/макроэкономист.md | макро-тренды, ставки, инфляция |

## Операции (operations/)
| Роль | Файл | Когда |
|---|---|---|
| COO / Опер. директор | operations/coo-оперционный-директор.md | процессы, эффективность |
| Project Manager | operations/project-manager.md | планирование, риски, timeline |
| Risk Manager | operations/risk-manager.md | риски, compliance, continuity |

## Код (code/)
| Роль | Файл | Когда |
|---|---|---|
| Разработчик | code/coder.md | написать/изменить код |
| Код-ревьюер (+security) | code/code-reviewer.md | проверить дифф |
| Отладчик | code/debugger.md | неизвестная причина сбоя |
| Рефакторер | code/refactorer.md | структура без смены поведения |
| Тестировщик | code/tester.md | покрыть тестами |
| DevOps/CI | code/devops.md | сборка/деплой/пайплайны |
| Архитектор ПО | code/архитектор-по.md | проектирование систем |
| Security Auditor | code/security-auditor.md | аудит безопасности |
| ML-инженер | code/ml-инженер.md | ML-модели, pipelines, feature engineering |

## Исследования (research/)
| Роль | Файл | Когда |
|---|---|---|
| Исследователь (scout) | research/researcher.md | сбор информации, план поиска |
| Глубокий аналитик | research/analyst.md | один вопрос/документ глубоко |
| Критик-скептик | research/fact-checker.md | красная волна (универсальная) |
| Аналитик данных | research/data-analyst.md | расчёты из данных |
| Синтезатор | research/synthesizer.md | разошлись критики → reconciliation |
| Научный писатель | research/science-writer.md | научный текст/отчёт |

## Маркетинг (marketing/)
| Роль | Файл | Когда |
|---|---|---|
| Маркетолог-стратег | marketing/marketing-strategist.md | кампания ДО текстов |
| Копирайтер | marketing/copywriter.md | продающий текст |
| SEO-специалист | marketing/seo.md | поисковая видимость |
| Редактор | marketing/editor.md | финальная полировка |
| Growth Hacker | marketing/growth-hacker.md | рост метрик, эксперименты |
| SMM-специалист | marketing/smm-специалист.md | соцсети, контент |
| PR-специалист | marketing/pr-специалист.md | публичные коммуникации |

## Продажи (sales/)
| Роль | Файл | Когда |
|---|---|---|
| Sales Strategist | sales/sales-strategist.md | pipeline, ICP, ценообразование |

## Право (legal/)
| Роль | Файл | Когда |
|---|---|---|
| Legal Analyst | legal/legal-analyst.md | договоры, compliance, GDPR/IP |

## HR (hr/)
| Роль | Файл | Когда |
|---|---|---|
| HR / Рекрутер | hr/hr-рекрутер.md | найм, вакансии, оценка |

## Психология (psychology/)
| Роль | Файл | Когда |
|---|---|---|
| Поведенческий аналитик | psychology/поведенческий-аналитик.md | поведение, biases, nudge |
| Специалист по переговорам | psychology/специалист-по-переговорам.md | BATNA, ZOPA, win-win |

## Тренды и инновации (trends/)
| Роль | Файл | Когда |
|---|---|---|
| Тренд-аналитик | trends/тренд-аналитик.md | тренды, сценарии, emerging tech |
| Design Thinking фасилитатор | trends/design-thinking-фасилитатор.md | инновации, ideation, прототипы |

## Образование (education/)
| Роль | Файл | Когда |
|---|---|---|
| Дизайнер обучения | education/дизайнер-обучения.md | curriculum, учебные программы |

---

**Всего ролей: 43** (+ фабрика ролей для нестандартных задач)

Правила применения:
- исполнитель получает ТОЛЬКО свой промт (context sharding);
- критики волн — всегда `research/fact-checker.md` или `code/code-reviewer.md`;
- после кругов с расхождением — `research/synthesizer.md`;
- 1 маленькая задача = 1 агент; если исполнителю нужен TODO — режь мельче;
- источник роли в файле — сверяй при адаптации.
