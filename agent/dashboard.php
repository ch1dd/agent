<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';
require_once __DIR__ . '/answer_files.php';

$user_id = $_SESSION['user_id'];
$chat = 'default';
$isAdmin = isset($_SESSION['role']) && $_SESSION['role'] === 'admin';

$ticketError = $_SESSION['ticket_error'] ?? null;
unset($_SESSION['ticket_error']);

$stmt = $pdo->prepare("
    SELECT q.id, q.question, q.answer, q.rag_result, q.files_json, q.ticket_id, q.created_at,
           t.status AS ticket_status, t.admin_answer, t.id AS manual_ticket
    FROM queries q
    LEFT JOIN tickets t ON q.ticket_id = t.id AND t.user_id = q.user_id AND t.query_id = q.id
    WHERE q.user_id = ? AND q.chat_id = ? 
    ORDER BY q.id DESC
");
$stmt->execute([$user_id, $chat]);
$queries = $stmt->fetchAll();
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Личный кабинет</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <a href="index.php">AI Agent</a>
        <nav>
            <?php if ($isAdmin): ?>
                <a href="admin.php">Админка</a>
            <?php endif; ?>
            <a href="dashboard.php">Кабинет</a>
            <a href="logout.php?token=<?= csrf_token() ?>">Выйти</a>
        </nav>
    </header>

    <main>
        <div class="card">
            <h1>Добро пожаловать, <?= htmlspecialchars($_SESSION['email']) ?>!</h1>
        </div>

        <?php if ($ticketError): ?>
            <div class="card message error">⚠️ <?= htmlspecialchars($ticketError) ?></div>
        <?php endif; ?>

<!-- Sticky поле ввода -->
<div class="sticky-question-form">
    <div class="card">
        <h2>Задать вопрос агенту</h2>
        <form id="questionForm" action="query.php" method="post" onsubmit="return submitQuestion(event)">
            <input type="hidden" name="request_id" value="<?= bin2hex(random_bytes(16)) ?>">
            <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
            <div class="form-group">
                <textarea id="questionInput" name="question" rows="3" 
                          placeholder="Ваш вопрос... (Enter — отправить, Shift+Enter — перенос строки)" 
                          required maxlength="1500"></textarea>
            </div>
            <button type="submit" id="submitBtn">Отправить</button>
        </form>
        <div id="loadingIndicator" class="loading-indicator" style="display: none;">
            <div class="spinner"></div>
            <span>Ваш вопрос обрабатывается...</span>
        </div>
    </div>
</div>

<div class="card">
    <h2>История запросов</h2>
    <div id="historyContainer">
        <?php if (count($queries) > 0): ?>
            <?php foreach ($queries as $row):
                $result = json_decode($row['rag_result'] ?? '', true) ?: [];
                $files = json_decode($row['files_json'] ?? '', true) ?: answer_files($result);
                $qid = (int)$row['id'];
            ?>
            <article class="query-item">
                <div class="query-label">Вопрос</div>
                <p><?= nl2br(htmlspecialchars($row['question'])) ?></p>
                <div class="query-label answer">Ответ</div>
                <p><?= nl2br(htmlspecialchars($row['answer'] ?? '')) ?></p>
                <?php foreach ($files as $file): ?>
                    <?php if (empty($file['version']) || empty($file['name'])) continue; ?>
                    <p><a href="download.php?query_id=<?= $qid ?>&amp;version=<?= rawurlencode($file['version']) ?>">Скачать <?= htmlspecialchars($file['name']) ?></a>
                       · <?= htmlspecialchars($file['location'] ?? '') ?></p>
                <?php endforeach; ?>
                <?php if (!empty($result['hits'])): ?>
                    <details><summary>Ранжирование источников</summary><ol>
                    <?php foreach ($result['hits'] as $i => $hit): ?>
                        <li><?= htmlspecialchars($hit['passage']['source'] . ' · ' . $hit['passage']['location']) ?>
                            · <?= $hit['dense_rank'] === null && $hit['lexical_rank'] === null ? 'Точное совпадение' : 'RRF ' . number_format($hit['score'], 4) ?>
                            <?= in_array($i, $result['used'] ?? [], true) ? ' · в ответе' : '' ?></li>
                    <?php endforeach; ?>
                    </ol></details>
                <?php endif; ?>
                <?php if ($row['manual_ticket']): ?>
                    <p>Заявка №<?= (int)$row['manual_ticket'] ?></p>
                    <?php if ($row['admin_answer'] !== null): ?>
                        <strong>Ответ администратора</strong><p><?= nl2br(htmlspecialchars($row['admin_answer'])) ?></p>
                    <?php endif; ?>
                <?php elseif (in_array($result['kind'] ?? '', ['unknown', 'off_topic'], true)): ?>
                    <form method="post" action="create_ticket.php">
                        <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                        <input type="hidden" name="query_id" value="<?= $qid ?>">
                        <button type="submit">Создать заявку админу</button>
                    </form>
                    <?php if ($email = filter_var(getenv('QUALITY_EMAIL'), FILTER_VALIDATE_EMAIL)): ?>
                        <a href="mailto:<?= htmlspecialchars($email) ?>?subject=<?= rawurlencode($row['question']) ?>">На почту</a>
                    <?php endif; ?>
                <?php endif; ?>
                <div class="query-meta">
                    <time><?= htmlspecialchars($row['created_at']) ?></time>
                    <form method="post" action="delete_query.php">
                        <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                        <input type="hidden" name="query_id" value="<?= $qid ?>">
                        <button type="submit">Удалить</button>
                    </form>
                </div>
            </article>
            <?php endforeach; ?>
        <?php else: ?>
            <div class="empty-state"><p>История запросов пуста</p></div>
        <?php endif; ?>
    </div>
</div>

<script>
async function submitQuestion(event) {
    event.preventDefault();
    
    const form = document.getElementById('questionForm');
    const input = document.getElementById('questionInput');
    const btn = document.getElementById('submitBtn');
    const loader = document.getElementById('loadingIndicator');
    
    const question = input.value.trim();
    if (!question || btn.disabled) return false;
    
    // Блокируем форму
    btn.disabled = true;
    loader.style.display = 'flex';
    
    try {
        const formData = new FormData(form);
        if (form.dataset.lastQuestion && form.dataset.lastQuestion !== question) {
            form.elements.request_id.value = generateRequestId();
            formData.set('request_id', form.elements.request_id.value);
        }
        form.dataset.lastQuestion = question;
        
        const response = await fetch('query_ajax.php', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) throw new Error('Ошибка сервера');
        
        const result = await response.json();
        
        if (result.success) {
            window.location.replace('dashboard.php');
        } else {
            alert('Ошибка: ' + (result.error || 'Неизвестная ошибка'));
        }
    } catch (error) {
        alert('Ошибка соединения: ' + error.message);
    } finally {
        btn.disabled = false;
        loader.style.display = 'none';
    }
    
    return false;
}

function generateRequestId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)))
        .map(b => b.toString(16).padStart(2, '0'))
        .join('');
}

// Enter для отправки
document.getElementById('questionInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('questionForm').requestSubmit();
    }
});
</script>


</body>
</html>
