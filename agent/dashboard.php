<?php
require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';

$user_id = $_SESSION['user_id'];
$chat = 'default';
$isAdmin = isset($_SESSION['role']) && $_SESSION['role'] === 'admin';

$ticketError = $_SESSION['ticket_error'] ?? null;
unset($_SESSION['ticket_error']);

$stmt = $pdo->prepare("
    SELECT q.id, q.question, q.answer, q.rag_result, q.ticket_id, q.created_at,
           t.status AS ticket_status, t.admin_answer, t.updated_at AS ticket_updated_at
    FROM queries q
    LEFT JOIN tickets t ON q.ticket_id = t.id
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
        <form id="questionForm" onsubmit="return submitQuestion(event)">
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
            <!-- ... ваш существующий код вывода истории ... -->
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
    if (!question) return false;
    
    // Блокируем форму
    btn.disabled = true;
    loader.style.display = 'flex';
    
    try {
        const formData = new FormData(form);
        formData.append('request_id', generateRequestId());
        
        const response = await fetch('query_ajax.php', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) throw new Error('Ошибка сервера');
        
        const result = await response.json();
        
        if (result.success) {
            // Добавляем новый вопрос в начало истории
            prependToHistory(result.query);
            input.value = '';
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

function prependToHistory(query) {
    const container = document.getElementById('historyContainer');
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();
    
    const queryHtml = `
        <div class="query-item" style="animation: fadeIn 0.5s;">
            <div>
                <div class="query-label">Вопрос:</div>
                <p>${escapeHtml(query.question).replace(/\n/g, '<br>')}</p>
            </div>
            <div style="margin-top: 12px;">
                <div class="query-label answer">Ответ:</div>
                <p style="color: var(--text-secondary);">${escapeHtml(query.answer).replace(/\n/g, '<br>')}</p>
            </div>
            <div class="query-meta">
                <span class="query-time">Только что</span>
            </div>
        </div>
    `;
    
    container.insertAdjacentHTML('afterbegin', queryHtml);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Enter для отправки
document.getElementById('questionInput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.getElementById('questionForm').dispatchEvent(new Event('submit'));
    }
});
</script>

<style>
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
}
</style>

    <script>
        // Enter для отправки, Shift+Enter для переноса
        document.getElementById('questionInput').addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                document.getElementById('questionForm').submit();
            }
        });

        // Показ индикатора загрузки
        function showLoading() {
            document.getElementById('loadingIndicator').style.display = 'flex';
            document.getElementById('submitBtn').disabled = true;
            return true;
        }
    </script>
</body>
</html>