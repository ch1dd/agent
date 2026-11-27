<?php
require 'auth.php';
requireAdmin();
require 'db.php';

$users = $pdo->query("SELECT id, email, role, created_at FROM users ORDER BY created_at DESC")->fetchAll();
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
            <a href="admin.php">Админка</a>
            <a href="logout.php">Выйти</a>
        </nav>
    </header>

    <main>
        <div class="card">
            <h1>Панель администратора</h1>
            <p style="color: var(--text-secondary); margin-top: 8px;">Всего пользователей: <?= count($users) ?></p>
        </div>

        <div class="card">
            <h2>Загрузка документов в базу знаний</h2>
            <form action="upload.php" method="POST" enctype="multipart/form-data">
                <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                <div class="form-group">
                    <input type="file" name="document" required>
                </div>
                <button type="submit">Загрузить</button>
            </form>
        </div>

        <div class="card">
            <h2>Список пользователей</h2>
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