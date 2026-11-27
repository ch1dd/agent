<?php
require __DIR__ . '/auth.php';
requireAdmin();
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';
require_once __DIR__ . '/withdrawals.php';
clearWithdrawnAnswers($pdo);

$ticket_id = (int)($_GET['id'] ?? 0);
if ($ticket_id <= 0) {
    http_response_code(400);
    exit('Неверный ID заявки');
}

$stmt = $pdo->prepare("SELECT t.*, u.email,q.request_id FROM tickets t JOIN users u ON t.user_id = u.id
    JOIN queries q ON q.id=t.query_id AND q.user_id=t.user_id WHERE t.id = ?");
$stmt->execute([$ticket_id]);
$ticket = $stmt->fetch();

if (!$ticket) {
    http_response_code(404);
    exit('Заявка не найдена');
}

$error = null;
$success = null;
$jobId = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    verify_csrf();
    
    $answer = trim($_POST['answer'] ?? '');
    $action = $_POST['action'] ?? 'reply'; // reply или publish
    
    if (empty($answer)) {
        $error = 'Введите текст ответа';
    } else {
        try {
            // 1. Сохраняем ответ в MySQL (всегда)
            $stmt = $pdo->prepare("UPDATE tickets SET admin_answer = ?, status = 'answered' WHERE id = ?");
            $stmt->execute([$answer, $ticket_id]);
            
            // 2. Если выбрано "Опубликовать в базе знаний" — создаём FAQ-файл
            if ($action === 'publish') {
                $faqContent = "Вопрос: " . $ticket['question'] . "\n\nОтвет: " . $answer . "\n";
                $faqFilename = "faq_ticket_" . $ticket_id . ".txt";
                
                // Создаём временный файл
                $tempFile = tempnam(sys_get_temp_dir(), 'faq_');
                file_put_contents($tempFile, $faqContent);
                
                // Загружаем через /admin/uploads
                $ch = curl_init('http://127.0.0.1:8765/admin/uploads?' . http_build_query([
                    'name' => $faqFilename, 'owner' => 'site:' . $ticket['user_id'], 'request' => $ticket['request_id']
                ]));
                $fp = fopen($tempFile, 'rb');
                $fileSize = filesize($tempFile);
                
                try {
                    curl_setopt_array($ch, [
                        CURLOPT_UPLOAD => true,
                        CURLOPT_CUSTOMREQUEST => 'POST',
                        CURLOPT_INFILE => $fp,
                        CURLOPT_INFILESIZE => $fileSize,
                        CURLOPT_RETURNTRANSFER => true,
                        CURLOPT_TIMEOUT => 120,
                        CURLOPT_HTTPHEADER => [
                            'Content-Type: application/octet-stream',
                            'Authorization: Bearer ' . RAG_REVIEW_TOKEN
                        ]
                    ]);
                    
                    $raw = curl_exec($ch);
                    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
                    
                    if ($raw === false || $code !== 202) {
                        throw new RuntimeException('Сервис не принял файл, HTTP ' . $code);
                    }
                    
                    $job = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
                    $jobId = $job['job_id'];
                    
                    // Сохраняем job_id в заявке
                    $pdo->prepare("UPDATE tickets SET job_id = ? WHERE id = ?")
                        ->execute([$jobId, $ticket_id]);
                    
                    $success = 'Ответ сохранён и отправлен на индексацию. Задание: ' . $jobId;
                } finally {
                    fclose($fp);
                    unlink($tempFile);
                }
            } else {
                $success = 'Ответ сохранён и отправлен сотруднику';
            }
            
            header('Location: ticket_reply.php?id=' . $ticket_id . '&success=' . urlencode($success));
            exit;
            
        } catch (Exception $e) {
            $error = 'Ошибка: ' . $e->getMessage();
        }
    }
}

if (isset($_GET['success'])) {
    $success = $_GET['success'];
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Ответ на заявку #<?= $ticket_id ?></title>
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
            <h1>Заявка #<?= $ticket_id ?></h1>
            <?php if ($success): ?>
                <div class="message success">✅ <?= htmlspecialchars($success) ?></div>
            <?php endif; ?>
            <?php if ($error): ?>
                <div class="message error"><?= htmlspecialchars($error) ?></div>
            <?php endif; ?>
        </div>

        <div class="card">
            <h2>Вопрос сотрудника</h2>
            <p><strong>От:</strong> <?= htmlspecialchars($ticket['email']) ?></p>
            <p style="margin-top: 8px; color: var(--text-secondary);"><?= nl2br(htmlspecialchars($ticket['question'])) ?></p>
        </div>

        <div class="card">
            <h2>Ваш ответ</h2>
            <form action="ticket_reply.php?id=<?= $ticket_id ?>" method="POST">
                <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                <div class="form-group">
                    <label for="answer">Текст ответа</label>
                    <textarea id="answer" name="answer" rows="6" required 
                              placeholder="Введите ответ..."><?= htmlspecialchars($ticket['admin_answer'] ?? '') ?></textarea>
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
                    «Ответить» — личный ответ сотруднику (виден только ему).<br>
                    «Добавить в базу знаний» — ответ станет доступен всем через поиск после индексации.
                </p>
            </form>
        </div>
    </main>
</body>
</html>
