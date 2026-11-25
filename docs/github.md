# Первый push ЭЛОМЕРА на GitHub

Команды ниже предназначены для PowerShell. Создайте на GitHub пустой репозиторий, выберите **Private** и скопируйте HTTPS-адрес. Для первого push оставьте флажки добавления README, gitignore и лицензии выключенными. Локальный проект уже содержит README и правила исключения файлов.

## Подготовить первый коммит

Откройте PowerShell в корне `ELOMER_web`, рядом с `run.bat`.

```powershell
git init -b main
git add -- .gitignore .gitattributes .dockerignore .github README.md docs examples/README.md backend frontend deploy run.bat
git status --short
git diff --cached --name-only
```

Просмотрите список перед коммитом. В нём должны быть исходники, документация и конфигурации сборки. Весь `backend/data/`, рабочие книги, документы, архивы и серверные файлы `deploy/users.caddy` и `deploy/Caddyfile` хранятся отдельно.

Если Git впервые просит имя и почту для коммита, укажите их для этого проекта.

```powershell
git config user.name "Ваше имя"
git config user.email "Ваш адрес или noreply-адрес GitHub"
```

Создайте коммит и подключите репозиторий. Подставьте адрес с вашей страницы GitHub вместо примера.

```powershell
git commit -m "Add ELOMER application"
git remote add origin https://github.com/OWNER/elomer.git
git remote -v
git push -u origin main
```

При запросе входа авторизуйтесь в GitHub. После публикации откройте вкладку **Actions**, чтобы проверить сборку интерфейса и импорт API. Для следующих изменений достаточно добавить нужные файлы, сделать коммит и выполнить `git push`.

[Инструкция GitHub по импорту проекта из командной строки](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github?platform=windows)

Публикация репозитория сохраняет и передаёт код. Чтобы приложение было доступно пользователям, разверните его отдельно по [инструкции для сервера](../deploy/ИНСТРУКЦИЯ.md). Для воспроизведения вашей рабочей установки подключите [копию данных](architecture.md#рабочие-данные).
