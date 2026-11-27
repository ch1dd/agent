# Подключение PHP-сайта к консультанту

Сайт отвечает за вход пользователя и историю в MySQL. Один Python-процесс обслуживает HTTP, удерживает локальный Qdrant и выполняет очередь индексации. Запускайте его один раз при старте сервера. PHP обращается к нему по локальному адресу. Для отдельного демо Streamlit можно использовать другой RAG_DATA_DIR.

## Запуск на сервере

Установите зависимости, языки OCR и модели по README. В окружении процесса Python задайте RAG_DATA_DIR (постоянная папка), RAG_DOCS_DIR (действующие регламенты), RAG_API_TOKEN и RAG_REVIEW_TOKEN (два разных случайных ключа). Эти же ключи задайте для PHP на сервере. В браузер их передавать нельзя. Перед первым запуском сервиса можно выполнить индексацию папки. При обновлении старой базы синхронизируйте исходную папку ещё раз для сохранения оригиналов. Для новых загрузок остановка сервиса не требуется.

~~~powershell
.\.venv\Scripts\python.exe indexer.py
.\.venv\Scripts\python.exe service.py
~~~

Адрес по умолчанию — http://127.0.0.1:8765. Порт задаётся RAG_API_PORT. При размещении в Linux команды те же с .venv/bin/python. Для автозапуска используйте менеджер служб сервера с рабочим каталогом проекта и постоянными путями к базе и оригиналам. На Windows при проблемах с CUDA можно задать RAG_NUM_GPU=0 для генерации на CPU. Таймаут одного обращения к Ollama задаётся RAG_OLLAMA_TIMEOUT в секундах.

## Данные сайта

В существующем проекте dashboard.php выводит queries, query.php вставляет заглушку, admin.php уже содержит форму с полем document, направленную в upload.php. Добавьте обработчик загрузки и форму проверки заявок в административном кабинете. У пользователя должен остаться доступ к своим вопросам и оригиналам найденных источников.

Пример однократной миграции существующей таблицы MySQL. Старые записи останутся в диалоге default. Для нового диалога создавайте новый chat_id и показывайте список диалогов только текущего пользователя.

~~~sql
ALTER TABLE queries
    ADD COLUMN chat_id VARCHAR(64) NOT NULL DEFAULT 'default',
    ADD COLUMN request_id CHAR(32) NULL,
    ADD COLUMN rag_result JSON NULL,
    ADD COLUMN ticket_id BIGINT NULL,
    ADD COLUMN human_answer TEXT NULL,
    ADD COLUMN reviewer VARCHAR(120) NULL,
    ADD COLUMN review_revision INT NOT NULL DEFAULT 0,
    ADD UNIQUE KEY uq_queries_request (user_id, chat_id, request_id),
    ADD INDEX ix_queries_chat (user_id, chat_id, id);
~~~

## Общий клиент для PHP

Создайте на стороне сайта rag_client.php. Функция вызывается только из обработчиков после проверки роли. Для MySQL и HTTP нужны расширения PDO MySQL и cURL.

~~~php
<?php
function rag(string $path, ?array $data = null, bool $admin = false): array {
    $key = getenv($admin ? 'RAG_REVIEW_TOKEN' : 'RAG_API_TOKEN');
    $ch = curl_init('http://127.0.0.1:8765' . $path);
    $headers = ['Authorization: Bearer ' . $key];
    if ($data !== null) {
        $headers[] = 'Content-Type: application/json';
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE));
    }
    curl_setopt_array($ch, [
        CURLOPT_HTTPHEADER => $headers, CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 5, CURLOPT_TIMEOUT => 1200
    ]);
    $raw = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    if ($raw === false || $code < 200 || $code >= 300) {
        throw new RuntimeException('Сервис консультанта недоступен, HTTP ' . $code);
    }
    return json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
}
~~~

## «Спросить консультанта»

В dashboard.php добавьте к форме скрытое поле request_id со значением bin2hex(random_bytes(16)). Повторная отправка той же формы использует прежний ID. В query.php сохраните проверку входа, POST, CSRF и длины вопроса. Имя владельца формируйте из серверной сессии. chat_id берите из выбранного диалога пользователя, например default для первой версии. Если PHP закрывает сессию до завершения запроса, сериализуйте параллельные вопросы одного диалога через именованную блокировку MySQL GET_LOCK на пару (user_id, chat_id) до чтения истории и RELEASE_LOCK в finally после INSERT. Это сохраняет порядок при работе из двух вкладок.

Вместо заглушки и прежнего INSERT используйте такой вызов. При ошибке сервиса оставьте возможность повторить тот же request_id. Таймауты PHP-FPM и веб-сервера должны позволять завершить запрос к модели.

~~~php
require_once 'rag_client.php';
$owner = 'site:' . $user_id;
$chat = 'default'; // либо выбранный chat_id из сессии
$id = $_POST['request_id'] ?? '';
if (!preg_match('/^[0-9a-f]{32}$/D', $id)) {
    throw new InvalidArgumentException('Некорректный request_id');
}
$recent = $pdo->prepare("SELECT question,answer,human_answer,rag_result FROM queries
    WHERE user_id=? AND chat_id=? AND rag_result IS NOT NULL
      AND JSON_UNQUOTE(JSON_EXTRACT(rag_result, '$.kind')) <> 'off_topic'
    ORDER BY id DESC LIMIT 5");
$recent->execute([$user_id, $chat]);
$previous = [];
foreach (array_reverse($recent->fetchAll()) as $r) {
    $meta = json_decode($r['rag_result'], true);
    $previous[] = ['question'=>$r['question'], 'text'=>$r['answer'],
        'human_answer'=>$r['human_answer'], 'kind'=>$meta['kind']];
}
$result = rag('/ask', ['owner'=>$owner, 'chat'=>$chat, 'request'=>$id,
    'question'=>$question, 'previous'=>$previous]);
$sql = "INSERT INTO queries(user_id,chat_id,request_id,question,answer,rag_result,ticket_id)
    VALUES(?,?,?,?,?,?,?) ON DUPLICATE KEY UPDATE request_id=VALUES(request_id)";
$pdo->prepare($sql)->execute([$user_id,$chat,$id,$question,$result['text'],
    json_encode($result, JSON_THROW_ON_ERROR|JSON_UNESCAPED_UNICODE),$result['ticket_id']]);
~~~

Сервис использует пять последних рабочих сообщений вместе с текущим вопросом. Старые строки с заглушками видны в истории, но не используются как знания. Удаление/очистка на сайте меняет контекст следующего запроса, так как PHP каждый раз передаёт previous, включая пустой массив для нового диалога. На стороне Python сохраняется запись ответа для идемпотентных повторов. Учтите её при задании срока хранения переписки.

## Цитаты, ранжирование и оригиналы

Сохранённый rag_result содержит text, kind, ticket_id, hits и used. hits идут в порядке убывания RRF. У каждого есть passage.source, passage.location, passage.body, score, dense_rank, lexical_rank и version. used — позиции с нуля в hits, которые подтвердили ответ. Под ответом выведите эти источники, а все hits — в раскрываемом списке. Экранируйте пользовательский текст и названия через htmlspecialchars.

Кнопка скачивания ведёт на PHP-обработчик с ID вопроса и номером источника. Обработчик выбирает queries по id **и user_id из сессии**, проверяет индекс внутри сохранённого hits и берёт оттуда version. Далее выполняет GET /originals/{version} с пользовательским серверным ключом и пересылает байты, Content-Type и Content-Disposition клиенту только при HTTP 200. Путь к файлу от браузера не принимается. Версия указывает на сохранённую копию оригинала или текстовую карточку утверждённого решения. Поэтому старый ответ продолжает открывать соответствующую редакцию.

## «Добавить документ»

В upload.php выполните require 'auth.php', requireAdmin(), проверку POST и verify_csrf(). Убедитесь, что токен в сессии и форме непустой. В текущем auth.php лишний второй session_start(); его можно убрать при подключении. Проверьте UPLOAD_ERR_OK, is_uploaded_file и размер до 64 МиБ. В форме admin.php для нескольких файлов добавьте multiple и name="document[]". Отправляйте каждый принятый файл отдельным запросом. Для одного файла из текущей формы:

~~~php
$f = $_FILES['document'];
if ($f['error'] !== UPLOAD_ERR_OK || !is_uploaded_file($f['tmp_name']) || $f['size'] > 64*1024*1024) {
    throw new RuntimeException('Ошибка загрузки файла');
}
$ch = curl_init('http://127.0.0.1:8765/admin/uploads?name=' . rawurlencode(basename($f['name'])));
$fp = fopen($f['tmp_name'], 'rb');
try {
    curl_setopt_array($ch, [
        CURLOPT_UPLOAD=>true, CURLOPT_CUSTOMREQUEST=>'POST', CURLOPT_INFILE=>$fp,
        CURLOPT_INFILESIZE=>$f['size'], CURLOPT_RETURNTRANSFER=>true, CURLOPT_TIMEOUT=>120,
        CURLOPT_HTTPHEADER=>['Content-Type: application/octet-stream',
            'Authorization: Bearer ' . getenv('RAG_REVIEW_TOKEN')]
    ]);
    $raw = curl_exec($ch);
    if ($raw === false || curl_getinfo($ch, CURLINFO_RESPONSE_CODE) !== 202) {
        throw new RuntimeException('Сервис не принял файл');
    }
    $job = json_decode($raw, true, 512, JSON_THROW_ON_ERROR)['job_id'];
} finally {
    fclose($fp);
}
~~~

Покажите номер задания сразу. GET /admin/jobs с административным ключом возвращает последние 200 заданий, состояние queued/running/done/failed и причину ошибки. При failed можно загрузить исправленный файл с тем же именем. Повторная загрузка того же имени заменяет прежнюю загруженную редакцию после успешной индексации. Папка исходных регламентов и загрузки через форму имеют отдельные пространства имён.

## Ответ администратора и публикация

В кабинете администратора GET /admin/tickets возвращает заявки, revision и диалог (поле dialogue — JSON-строка). Покажите поля «Вопрос с условиями» и «Ответ», кнопки «Ответить сотруднику» и «Ответить и добавить в базу знаний». Обработчик проверяет администратора, метод POST и CSRF. Имя проверяющего берите из серверной сессии.

~~~php
$result = rag('/admin/tickets/' . (int)$ticketId, [
    'revision'=>(int)$revision, 'topic'=>$topic, 'answer'=>$answer,
    'reviewer'=>$_SESSION['email'], 'publish'=>($action === 'publish')
], true);
~~~

Личный ответ сразу сохранён в заявке. Публикация также ставит запись в постоянную очередь. Пока Qdrant недоступен, пользователь может прочитать ответ, а сервис повторяет публикацию с задержкой. Для исправления используйте текущую revision. После публикации следующая редакция заменяет прежнюю в поиске. Для перевода решения в личный ответ отправьте новую редакцию с publish=false.

В dashboard.php для своей строки с ticket_id получайте карточку из /tickets/{id}?owner=site%3A... с пользовательским ключом. Синхронизируйте ответ в MySQL при показе диалога и перед выборкой контекста, если известно об изменении решения. Пример для строки, уже выбранной по user_id:

~~~php
$card = rag('/tickets/' . (int)$row['ticket_id'] . '?owner=' . rawurlencode('site:' . $user_id));
if ($card['answer'] !== null) {
    $pdo->prepare('UPDATE queries SET human_answer=?,reviewer=?,review_revision=?
        WHERE id=? AND user_id=? AND review_revision<?')
        ->execute([$card['answer'],$card['reviewer'],$card['revision'],
                   $row['id'],$user_id,$card['revision']]);
}
~~~

На странице покажите human_answer и reviewer отдельно от первоначального ответа бота. Уведомление можно обновлять кнопкой или опросом. MySQL хранит историю сайта; SQLite рядом с Python хранит заявки, задания и состояние публикации; Qdrant обслуживает поиск только по действующим версиям. Ответы добавляются в знания исключительно после действия администратора.

## Приёмка на сайте

1. Два разных пользователя задают вопросы. После повторного входа каждый открывает свой диалог. Удалённый вопрос исключается из контекста.
2. Отправьте полный вопрос, затем постороннюю реплику и повторите вопрос. Источник совпадает, лишняя заявка не создаётся. Проверьте также короткое продолжение «А с юридическим?».
3. Начните загрузку двух файлов, одновременно задайте вопрос по ранее готовому документу. Скачайте источник, загрузите новую редакцию и сравните оригинал из старого ответа.
4. Создайте заявку на отсутствующий факт. Сначала дайте личный ответ. Затем опубликуйте исправленную редакцию и дождитесь done. Другой пользователь должен найти утверждённое решение.
5. Перезапустите сервис с прежним RAG_DATA_DIR и откройте тот же диалог. История на сайте и статусы задач должны сохраниться.
