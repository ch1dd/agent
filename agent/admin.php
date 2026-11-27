<?php
require __DIR__ . '/auth.php';
requireAdmin();
require __DIR__ . '/db.php';
require_once __DIR__ . '/rag_client.php';

$users = $pdo->query("SELECT id, email, role, created_at FROM users ORDER BY created_at DESC")->fetchAll();

// Загрузка списка заявок
$stmt = $pdo->query("
    SELECT t.id, t.question, t.status, t.created_at, u.email as user_email 
    FROM tickets t
    JOIN users u ON t.user_id = u.id
    ORDER BY t.created_at DESC
");
$tickets = $stmt->fetchAll();

$jobs = [];
$jobsError = null;
try {
    $jobs = rag('/admin/jobs', null, true);
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
                                <span style="color: var(--primary);">📤 Индексируется</span>
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