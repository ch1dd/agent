<?php
require __DIR__ . '/auth.php';
requireAdmin();
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';
require_once __DIR__ . '/withdrawals.php';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    verify_csrf();
    $links = $pdo->query("SELECT t.id AS ticket,t.user_id AS `user`,q.request_id AS request
        FROM tickets t JOIN queries q ON q.id=t.query_id AND q.user_id=t.user_id
        WHERE q.request_id IS NOT NULL AND t.job_id IS NOT NULL")->fetchAll(PDO::FETCH_ASSOC);
    rag('/admin/documents', ['source' => $_POST['source'] ?? '', 'links' => $links], true);
    clearWithdrawnAnswers($pdo);
    header('Location: admin.php');
    exit;
}
clearWithdrawnAnswers($pdo);
$documents = rag('/admin/documents', null, true);

$users = $pdo->query("SELECT id, email, role, created_at FROM users ORDER BY created_at DESC")->fetchAll();

// Загрузка списка заявок
$stmt = $pdo->query("
    SELECT t.id, t.question, t.status, t.job_id, t.created_at, u.email as user_email
    FROM tickets t
    JOIN users u ON t.user_id = u.id
    ORDER BY t.created_at DESC
");
$tickets = $stmt->fetchAll();

$jobs = [];
$jobsError = null;
try {
    $jobs = array_column(rag('/admin/jobs', null, true), null, 'id');
} catch (Exception $e) {
    $jobsError = $e->getMessage();
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Админка</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <a href="index.php">AI Agent</a>
        <nav>
            <a href="dashboard.php">Кабинет</a>
            <a href="admin.php">Админка</a>
            <a href="logout.php?token=<?= csrf_token() ?>">Выйти</a>
        </nav>
    </header>
    <main>
        <div class="card">
            <h2>Документы базы знаний</h2>
            <?php foreach ($documents as $doc): ?>
                <form method="post" onsubmit="return confirm('Удалить этот источник, его редакции и связанные ответы?')">
                    <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                    <input type="hidden" name="source" value="<?= htmlspecialchars($doc['source'], ENT_QUOTES) ?>">
                    <?= htmlspecialchars($doc['source']) ?>
                    <button type="submit">Удалить</button>
                </form>
            <?php endforeach; ?>
        </div>
        <div class="card">
            <h1>Панель администратора</h1>
            <p style="color: var(--text-secondary); margin-top: 8px;">
                Пользователей: <?= count($users) ?> | 
                Заявок: <?= count($tickets) ?>
            </p>
        </div>

        <div class="card">
            <h2>Загрузка документов в базу знаний</h2>
            <form action="upload.php" method="POST" enctype="multipart/form-data">
                <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                <div class="form-group">
                    <label for="document">Выберите файл (PDF, DOCX, PNG, JPEG до 64 МБ)</label>
                    <input type="file" id="document" name="document" required>
                </div>
                <div class="form-group">
                    <label for="metadata">Метаданные (необязательно, JSON)</label>
                    <textarea id="metadata" name="metadata" rows="3" 
                              placeholder='{"number":"123","title":"Приказ","kind":"order","issued":"2026-01-01","effective":"2026-01-15"}'></textarea>
                </div>
                <button type="submit">Загрузить</button>
            </form>
        </div>



    <div class="card">
        <h2>Заявки от сотрудников (<?= count($tickets) ?>)</h2>
        <?php if (empty($tickets)): ?>
            <p style="color: var(--text-secondary);">Заявок пока нет</p>
        <?php else: ?>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Сотрудник</th>
                        <th>Вопрос</th>
                        <th>Статус</th>
                        <th>Публикация</th>
                        <th>Действия</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($tickets as $ticket): ?>
                    <tr>
                        <td>#<?= (int)$ticket['id'] ?></td>
                        <td><?= htmlspecialchars($ticket['user_email']) ?></td>
                        <td><?= htmlspecialchars(mb_strimwidth($ticket['question'], 0, 60, '...')) ?></td>
                        <td>
                            <?php if ($ticket['status'] === 'open'): ?>
                                <span style="color: #ff9800;">Ожидает</span>
                            <?php else: ?>
                                <span style="color: var(--success);">✓ Отвечено</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if (!empty($ticket['job_id'])): ?>
                                <?php $job = $jobs[$ticket['job_id']] ?? []; ?>
                                №<?= (int)$ticket['job_id'] ?> · <?= htmlspecialchars(['done'=>'Опубликовано','queued'=>'В очереди','running'=>'Индексируется','failed'=>'Ошибка'][$job['status'] ?? ''] ?? 'Статус недоступен') ?>
                                <?= htmlspecialchars($job['error'] ?? '') ?>
                            <?php else: ?>
                                <span style="color: var(--text-secondary);">—</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <a href="ticket_reply.php?id=<?= (int)$ticket['id'] ?>" class="btn btn-small">
                                <?= $ticket['status'] === 'open' ? 'Ответить' : 'Изменить' ?>
                            </a>
                        </td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        <?php endif; ?>
    </div>
        <div class="card">
            <h2>Список пользователей (<?= count($users) ?>)</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Email</th>
                        <th>Роль</th>
                        <th>Дата регистрации</th>
                    </tr>
                </thead>
                <tbody>
                    <?php foreach ($users as $user): ?>
                    <tr>
                        <td><?= $user['id'] ?></td>
                        <td><?= htmlspecialchars($user['email']) ?></td>
                        <td><?= $user['role'] ?></td>
                        <td><?= date('d.m.Y H:i', strtotime($user['created_at'])) ?></td>
                    </tr>
                    <?php endforeach; ?>
                </tbody>
            </table>
        </div>
    </main>
</body>
</html>
