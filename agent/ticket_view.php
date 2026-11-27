<?php
require __DIR__ . '/auth.php';
requireAdmin();
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';

$ticketId = (int)($_GET['id'] ?? 0);
if ($ticketId <= 0) {
    http_response_code(400);
    exit('Неверный ID');
}

// Получаем все заявки и ищем нужную
$ticket = null;
$error = null;
try {
    $tickets = rag('/admin/tickets', null, true);
    foreach ($tickets as $t) {
        if ((int)$t['id'] === $ticketId) {
            $ticket = $t;
            break;
        }
    }
    if (!$ticket) {
        $error = 'Заявка не найдена';
    }
} catch (Exception $e) {
    $error = $e->getMessage();
}

// Обработка ответа
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    verify_csrf();
    
    $answer = trim($_POST['answer'] ?? '');
    $topic = trim($_POST['topic'] ?? '');
    $action = $_POST['action'] ?? 'reply';
    
    if (empty($answer)) {
        $error = 'Введите ответ';
    } else {
        try {
            $result = rag('/admin/tickets/' . $ticketId, [
                'revision' => (int)($ticket['revision'] ?? 0),
                'topic' => $topic,
                'answer' => $answer,
                'reviewer' => $_SESSION['email'],
                'publish' => ($action === 'publish'),
            ], true);
            
            header('Location: ticket_view.php?id=' . $ticketId . '&saved=1');
            exit;
        } catch (Exception $e) {
            $error = $e->getMessage();
        }
    }
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Заявка #<?= $ticketId ?></title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <a href="index.php">AI Agent</a>
        <nav>
            <a href="admin.php">Админка</a>
            <a href="logout.php?token=<?= csrf_token() ?>">Выйти</a>
        </nav>
    </header>

    <main>
        <div class="card">
            <h1>Заявка #<?= $ticketId ?></h1>
            <?php if (isset($_GET['saved'])): ?>
                <div class="message success">Ответ сохранён</div>
            <?php endif; ?>
            <?php if ($error): ?>
                <div class="message error"><?= htmlspecialchars($error) ?></div>
            <?php endif; ?>
        </div>

        <?php if ($ticket): ?>
            <div class="card">
                <h2>Вопрос сотрудника</h2>
                <p><strong>От:</strong> <?= htmlspecialchars($ticket['owner'] ?? '—') ?></p>
                <p style="margin-top: 8px;"><strong>Вопрос:</strong></p>
                <p style="color: var(--text-secondary);"><?= nl2br(htmlspecialchars($ticket['question'] ?? '')) ?></p>
                
                <?php if (!empty($ticket['answer'])): ?>
                    <p style="margin-top: 16px;"><strong>Текущий ответ:</strong></p>
                    <p style="color: var(--text-secondary);"><?= nl2br(htmlspecialchars($ticket['answer'])) ?></p>
                    <?php if (!empty($ticket['reviewer'])): ?>
                        <p style="margin-top: 8px; font-size: 13px; color: var(--text-secondary);">
                            Ответил: <?= htmlspecialchars($ticket['reviewer']) ?>
                        </p>
                    <?php endif; ?>
                <?php endif; ?>
            </div>

            <div class="card">
                <h2>Ответить сотруднику</h2>
                <form action="ticket_view.php?id=<?= $ticketId ?>" method="POST">
                    <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                    <div class="form-group">
                        <label for="topic">Тема (кратко)</label>
                        <input type="text" id="topic" name="topic" 
                               value="<?= htmlspecialchars($ticket['topic'] ?? '') ?>"
                               placeholder="Например: Хранение бумажных оригиналов">
                    </div>
                    <div class="form-group">
                        <label for="answer">Ответ</label>
                        <textarea id="answer" name="answer" rows="6" required 
                                  placeholder="Введите ответ сотруднику..."><?= htmlspecialchars($ticket['answer'] ?? '') ?></textarea>
                    </div>
                    <div style="display: flex; gap: 12px; margin-top: 16px;">
                        <button type="submit" name="action" value="reply" class="btn btn-secondary">
                            💬 Ответить сотруднику
                        </button>
                        <button type="submit" name="action" value="publish" class="btn btn-primary">
                            📚 Ответить и добавить в базу знаний
                        </button>
                    </div>
                    <p style="margin-top: 12px; font-size: 13px; color: var(--text-secondary);">
                        «Ответить» — личный ответ сотруднику.<br>
                        «Добавить в базу знаний» — ответ станет доступен всем через поиск.
                    </p>
                </form>
            </div>
        <?php else: ?>
            <div class="card">
                <p>Заявка не найдена</p>
                <a href="admin.php" class="btn btn-primary">Назад</a>
            </div>
        <?php endif; ?>
    </main>
</body>
</html>