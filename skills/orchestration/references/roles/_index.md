# Каталог ролей: каскадный выбор (домен → поддомен → роль)

## Как выбирать роль (оркестратору)

**КАСКАД: от общего к узкому.** Не листай 103 файла — спускайся по уровням:

```
Уровень 1: ДОМЕН — какой тип задачи? (код / бизнес / финансы / ...)
Уровень 2: ПОДДОМЕН — какой аспект? (в бизнесе: стратегия? операции? анализ?)
Уровень 3: РОЛЬ — какой конкретный навык? (в стратегии: GTM? BMC? pricing?)
```

Правила:
1. Задали задачу → определи ДОМЕН (уровень 1)
2. Внутри домена → определи ПОДДОМЕН (уровень 2, таблица ниже)
3. Внутри поддомена → выбери РОЛЬ по колонке «Когда» (уровень 3)
4. Подходящей роли нет → ФАБРИКА: создай по `_template.md` и добавь сюда
5. Две роли подходят одинаково → возьми более узкую (специализированную)

---

## Уровень 1: ДОМЕНЫ

| Домен | Каталог | Когда |
|---|---|---|
| **Код** | code/ + tech/ | написать/изменить/проверить/отладить код, инфраструктура |
| **Бизнес** | business/ | стратегия, процессы, org, GTM, churn, CS |
| **Финансы** | finance/ | метрики, модели, оценка, инвестиции, pricing |
| **Исследования** | research/ | сбор информации, анализ, факт-чек |
| **Маркетинг** | marketing/ | тексты, кампании, SEO, соцсети, реклама |
| **Продажи** | sales/ | pipeline, outreach, переговоры, closing |
| **Данные** | data/ | SQL, дашборды, A/B, когорты, аномалии |
| **Психология** | psychology/ | поведение, мотивация, доверие, трение |
| **Тренды** | trends/ | технологии, сценарии, дизрупция, регуляции |
| **Операции** | operations/ | COO, PM, риски |
| **Право** | legal/ | договоры, compliance, GDPR, IP |
| **HR** | hr/ | найм, вакансии, онбординг |
| **Образование** | education/ | curriculum, learning design |
| **Специализированные** | specialized/ | переговоры, storytelling, кризис, отраслевые |

---

## Уровень 2-3: ПОДДОМЕНЫ → РОЛИ

### Код → Разработка
| Роль | Файл | Когда |
|---|---|---|
| Разработчик | code/coder.md |通用 изменить код |
| React Component Designer | code/react-component-designer.md | React UI, hooks, state |
| API Endpoint Implementer | code/api-endpoint-implementer.md | один endpoint: route→handler |
| GraphQL Schema Designer | code/graphql-schema-designer.md | GraphQL types, resolvers |
| State Management Architect | code/state-management-architect.md | Redux/Zustand, когда какой |
| CSS Layout Specialist | code/css-layout-specialist.md | flexbox, grid, responsive |
| TypeScript Type Designer | code/typescript-type-designer.md | generics, utility types |
| Auth Flow Implementer | code/auth-flow-implementer.md | JWT/OAuth/session, middleware |
| WebSocket Implementer | code/websocket-implementer.md | real-time, SSE, reconnect |
| Error Handling Designer | code/error-handling-designer.md | error boundaries, retry |
| Logger Setup | code/logger-setup.md | structured logging |

### Код → Качество
| Роль | Файл | Когда |
|---|---|---|
| Код-ревьюер (+security) | code/code-reviewer.md | проверить дифф |
| Code Review Checklist | code/code-review-checklist.md | структурированное ревью |
| Отладчик | code/debugger.md | неизвестная причина |
| Тестировщик | code/tester.md | покрыть тестами |
| Test Pyramid Designer | code/test-pyramid-designer.md | стратегия тестирования |
| Mock/Stub Specialist | code/mock-stub-specialist.md | моки, contract tests |
| Рефакторер | code/refactorer.md | структура без смены поведения |
| Refactoring Patterns | code/refactoring-pattern-specialist.md | конкретные паттерны |
| Technical Debt Assessor | code/technical-debt-assessor.md | аудит техдолга |

### Код → Инфраструктура
| Роль | Файл | Когда |
|---|---|---|
| DevOps/CI | code/devops.md |通用 сборка/деплой |
| CI/CD Pipeline Designer | tech/ci-cd-pipeline-designer.md | GitHub Actions, deployment |
| Docker Optimization | tech/docker-optimization.md | image size, multi-stage |
| Database Optimizer | tech/database-optimizer.md | query optimization, indexing |
| Database Schema Designer | code/database-schema-designer.md | таблицы, миграции |
| Deployment Strategy | tech/deployment-strategy-designer.md | blue-green, canary |
| Monitoring/Alerting | tech/monitoring-alerting-setup.md | метрики, дашборды, алерты |
| Incident Response | tech/incident-response.md | прод упал, postmortem |

### Код → Архитектура и meta
| Роль | Файл | Когда |
|---|---|---|
| Архитектор ПО | code/архитектор-по.md | проектирование систем |
| API Design Reviewer | tech/api-design-reviewer.md | REST design, versioning |
| API Docs Writer | tech/api-documentation-writer.md | OpenAPI, документация |
| Code Docs Generator | code/code-documentation-generator.md | JSDoc, docstrings |
| Git Workflow | code/git-workflow-specialist.md | branching, conflicts |
| Dependency Upgrader | code/dependency-upgrade-specialist.md | breaking changes, миграции |
| ML-инженер | code/ml-инженер.md | ML-модели, pipelines |
| Security Auditor | code/security-auditor.md | аудит безопасности |
| Performance Profiler | tech/performance-profiler.md | profiling, bottlenecks |

### Бизнес → Стратегия
| Роль | Файл | Когда |
|---|---|---|
| Стратег | business/стратег.md |通用 направление, позиционирование |
| GTM Strategist | business/gtm-strategist.md | вывод на рынок |
| BMC Facilitator | business/business-model-canvas-facilitator.md | бизнес-модель |
| Market Sizing | business/market-sizing-analyst.md | TAM/SAM/SOM |
| Competitive Intel | business/competitive-intel-scanner.md | разведка |
| Конкурентный аналитик | business/конкурентный-аналитик.md | сравнение конкурентов |
| Partnership/BD | business/partnership-bd.md | партнёрства |

### Бизнес → Операции и рост
| Роль | Файл | Когда |
|---|---|---|
| COO | operations/coo-оперционный-директор.md |通用 процессы |
| Churn Analyst | business/churn-analyst.md | почему уходят |
| CS Playbook | business/customer-success-playbook.md | onboarding, renewal |
| Org Design | business/org-design-consultant.md | структура команды |
| Project Manager | operations/project-manager.md | планирование, риски |
| Risk Manager | operations/risk-manager.md | риски, compliance |
| Business Model Analyst | business/business-model-canvas-facilitator.md | BMC |

### Финансы → Метрики
| Роль | Файл | Когда |
|---|---|---|
| SaaS Metrics | finance/saas-metrics-specialist.md | MRR/NRR/churn |
| Unit Economics | finance/unit-economics-analyst.md | CAC/LTV, payback |
| Burn Rate/Runway | finance/burn-rate-runway.md | сколько до конца денег |

### Финансы → Оценка и модели
| Роль | Файл | Когда |
|---|---|---|
| CFO | finance/cfo-финсовый-директор.md |通用 фин. стратегия |
| DCF Valuation | finance/dcf-valuation.md | оценка стоимости |
| Fin. Model Builder | finance/financial-model-builder.md | 3-statement model |
| Финансовый аналитик | finance/финансовый-аналитик.md |通用 модели, оценка |
| Pricing Model | finance/pricing-model-analyst.md | стратегии цены |

### Финансы → Инвестиции
| Роль | Файл | Когда |
|---|---|---|
| Венчурный инвестор | finance/венчурный-инвестор.md | due diligence |
| Term Sheet | finance/term-sheet-analyst.md | условия инвестиций |
| Cap Table | finance/cap-table-analyst.md | dilution, ESOP |
| Fundraising Deck | finance/fundraising-deck-reviewer.md | pitch deck review |
| Макроэкономист | finance/макроэкономист.md | макро-тренды |

### Маркетинг → Стратегия и тексты
| Роль | Файл | Когда |
|---|---|---|
| Маркетолог-стратег | marketing/marketing-strategist.md |通用 бриф до текстов |
| Копирайтер | marketing/copywriter.md |通用 продающий текст |
| Brand Voice | marketing/brand-voice-guardian.md | tone of voice |
| Storytelling Coach | specialized/storytelling-coach.md | narrative |
| Email Sequences | marketing/email-sequence-writer.md | drip campaigns |
| Video Script | marketing/video-script-writer.md | YouTube/TikTok |

### Маркетинг → Реклама
| Роль | Файл | Когда |
|---|---|---|
| Google Ads Copy | marketing/ad-copy-google.md | RSA, keywords |
| Meta Ads Copy | marketing/ad-copy-meta.md | Instagram/Facebook |
| Ad Analyst | marketing/competitive-ad-analyst.md | реклама конкурентов |
| Landing Optimizer | marketing/landing-page-optimizer.md | конверсия, A/B |
| Influencer Strategist | marketing/influencer-strategist.md | инфлюенсеры |

### Маркетинг → Каналы
| Роль | Файл | Когда |
|---|---|---|
| SEO | marketing/seo.md |通用 поисковая видимость |
| ASO | marketing/app-store-optimizer.md | мобильные приложения |
| SMM | marketing/smm-специалист.md |通用 соцсети |
| Community Manager | marketing/community-manager.md | Discord/Telegram |
| Content Calendar | marketing/content-calendar-planner.md | план контента |
| Affiliate | marketing/affiliate-program-designer.md | партнёрская программа |
| PR | marketing/pr-специалист.md | коммуникации |
| Growth Hacker | marketing/growth-hacker.md | эксперименты роста |
| Редактор | marketing/editor.md | полировка |

### Продажи
| Роль | Файл | Когда |
|---|---|---|
| Sales Strategist | sales/sales-strategist.md |通用 pipeline, ICP |
| Cold Outreach | sales/cold-outreach-writer.md | холодные письма |
| Pipeline Analyst | sales/sales-pipeline-analyst.md | конверсии |
| Negotiation/Closing | sales/negotiation-closing.md | закрытие |
| Sales Enablement | sales/sales-enablement.md | playbooks |

### Исследования
| Роль | Файл | Когда |
|---|---|---|
| Исследователь (scout) | research/researcher.md |通用 сбор информации |
| Глубокий аналитик | research/analyst.md | один вопрос глубоко |
| Критик-скептик | research/fact-checker.md | красная волна (универсальная) |
| Аналитик данных | research/data-analyst.md |通用 расчёты |
| Синтезатор | research/synthesizer.md | reconciliation |
| Научный писатель | research/science-writer.md | научный текст |
| Консультант (McKinsey) | business/консультант-mckinsey-frameworks.md | issue trees, MECE |
| Бизнес-аналитик | business/бизнес-аналитик.md | as-is/to-be/gap |
| Product Manager | business/product-manager.md | roadmap, приоритизация |
| User Research | specialized/user-research-interviewer.md | интервью, JTBD |
| Empathy Mapping | specialized/empathy-map-facilitator.md | personas, journey |

### Данные
| Роль | Файл | Когда |
|---|---|---|
| SQL Writer | data/sql-query-writer.md | сложные SQL |
| Dashboard Designer | data/dashboard-designer.md | метрики, viz |
| A/B Test Analyst | data/ab-test-analyst.md | significance, sample size |
| Data Pipeline | data/data-pipeline-architect.md | ETL/ELT |
| Cohort Analyst | data/cohort-analyst.md | retention |
| Anomaly Detector | data/anomaly-detector.md | outliers, alerts |

### Психология
| Роль | Файл | Когда |
|---|---|---|
| Поведенческий аналитик | psychology/поведенческий-аналитик.md |通用 biases, nudge |
| Motivation Designer | psychology/motivation-designer.md | Self-Determination Theory |
| Habit Formation | psychology/habit-formation-specialist.md | Hook Model |
| Trust Builder | psychology/trust-builder.md | Cialdini, social proof |
| Friction Analyst | psychology/friction-analyst.md | cognitive load, UX |
| Persuasion Ethics | psychology/persuasion-ethics-auditor.md | dark patterns |

### Тренды
| Роль | Файл | Когда |
|---|---|---|
| Тренд-аналитик | trends/тренд-аналитик.md |通用 тренды, сценарии |
| Technology Radar | trends/technology-radar-facilitator.md | adopt/trial/assess/hold |
| Scenario Planning | trends/scenario-planning-facilitator.md | сценарии 2x2 |
| Disruption Analyst | trends/disruption-analyst.md | Christensen, disruption |
| Patent Landscape | trends/patent-landscape-analyst.md | IP-риски |
| Regulatory Scanner | trends/regulatory-horizon-scanner.md | надвигающиеся регуляции |
| Design Thinking | trends/design-thinking-фасилитатор.md | ideation, прототипы |

### Специализированные (отраслевые + мета)
| Роль | Файл | Когда |
|---|---|---|
| Harvard Negotiation | specialized/negotiation-harvard.md | сложные переговоры |
| Cognitive Bias | specialized/cognitive-bias-analyst.md | психология убеждения |
| Presentation Designer | specialized/presentation-designer.md | слайды, data viz |
| Crisis Communications | specialized/crisis-communications.md | кризисный PR |
| Accessibility Auditor | specialized/accessibility-auditor.md | WCAG, screen readers |
| Fintech Compliance | specialized/fintech-compliance.md | KYC/AML, PCI |
| Healthtech Privacy | specialized/healthtech-privacy.md | HIPAA, health data |
| Edtech Instructional | specialized/edtech-instructional.md | learning design |
| E-commerce Optimizer | specialized/ecommerce-optimizer.md | cart, checkout |
| Marketplace Analyst | specialized/marketplace-analyst.md | supply/demand |
| SaaS Onboarding | specialized/saas-onboarding-designer.md | activation |
| Legal Analyst | legal/legal-analyst.md | договоры, GDPR |
| HR/Рекрутер | hr/hr-рекрутер.md | найм, вакансии |
| Дизайнер обучения | education/дизайнер-обучения.md | curriculum |

---

**Всего: 133 роли** (каскадный выбор: домен → поддомен → роль) (+ фабрика ролей для нестандартных)

Правила:
- Каскад: домен → поддомен → роль (не листай всё)
- 1 маленькая задача = 1 агент
- Исполнитель получает ТОЛЬКО свой промт (context sharding)
- Критики: fact-checker (не-код) / code-reviewer (код)
- Расхождение → волна расхождений → синтезатор
- Нет роли → фабрика по _template.md → останется навсегда
