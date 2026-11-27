<?php
require 'auth.php';
require 'db.php';

$user_id = $_SESSION['user_id'];
$search = trim($_GET['search'] ?? '');

// Загружаем историю с учётом поиска
if ($search !== '') {
    $stmt = $pdo->prepare(
        "SELECT id, question, answer, created_at FROM queries 
         WHERE user_id = ? AND (question LIKE ? OR answer LIKE ?) 
         ORDER BY created_at DESC"
    );
    $like = '%' . $search . '%';
    $stmt->execute([$user_id, $like, $like]);
} else {
    $stmt = $pdo->prepare(
        "SELECT id, question, answer, created_at FROM queries 
         WHERE user_id = ? ORDER BY created_at DESC"
    );
    $stmt->execute([$user_id]);
}
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
            <a href="dashboard.php">Кабинет</a>
            <a href="logout.php">Выйти</a>
        </nav>
    </header>

    <main>
        <div class="card">
            <h1>Добро пожаловать, <?= htmlspecialchars($_SESSION['email']) ?>!</h1>
            <p style="color: var(--text-secondary); margin-top: 8px;">Ваш ID: <?= $_SESSION['user_id'] ?></p>
        </div>

        <div class="card">
            <h2>Задать вопрос агенту</h2>
            <form action="query.php" method="POST">
                <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                <div class="form-group">
                    <textarea name="question" rows="4" placeholder="Ваш вопрос..." required></textarea>
                </div>
                <button type="submit">Отправить</button>
            </form>
        </div>

        <div class="card">
            <h2>История запросов</h2>
            
            <!-- Поиск -->
            <form action="dashboard.php" method="GET" class="search-form">
                <input type="text" name="search" placeholder="Поиск по вопросам и ответам..." 
                       value="<?= htmlspecialchars($search) ?>">
                <button type="submit">Найти</button>
                <?php if ($search !== ''): ?>
                    <a href="dashboard.php" class="btn btn-secondary" style="text-decoration: none; display: inline-flex; align-items: center;">Сбросить</a>
                <?php endif; ?>
            </form>

            <?php if (count($queries) > 0): ?>
                <!-- Панель действий -->
                <div class="actions-bar">
                    <span style="color: var(--text-secondary);">
                        Найдено: <?= count($queries) ?>
                        <?php if ($search !== ''): ?>
                            по запросу «<?= htmlspecialchars($search) ?>»
                        <?php endif; ?>
                    </span>
                    <form action="clear_history.php" method="POST" 
                          onsubmit="return confirm('Удалить всю историю? Это действие нельзя отменить.');">
                        <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                        <button type="submit" class="btn-danger btn-small">Очистить всю историю</button>
                    </form>
                </div>

                <!-- Список запросов -->
                <?php foreach ($queries as $query): ?>
                    <div class="query-item">
                        <div>
                            <div class="query-label">Вопрос:</div>
                            <p><?= nl2br(htmlspecialchars($query['question'])) ?></p>
                        </div>
                        <div style="margin-top: 12px;">
                            <div class="query-label answer">Ответ:</div>
                            <p style="color: var(--text-secondary);"><?= nl2br(htmlspecialchars($query['answer'])) ?></p>
                        </div>
                        <div class="query-meta">
                            <span class="query-time">
                                <?= date('d.m.Y H:i', strtotime($query['created_at'])) ?>
                            </span>
                            <form action="delete_query.php" method="POST" class="confirm-delete"
                                  onsubmit="return confirm('Удалить этот запрос?');">
                                <input type="hidden" name="csrf_token" value="<?= csrf_token() ?>">
                                <input type="hidden" name="query_id" value="<?= $query['id'] ?>">
                                <button type="submit" class="btn-danger btn-small">Удалить</button>
                            </form>
                        </div>
                    </div>
                <?php endforeach; ?>
            <?php else: ?>
                <div class="empty-state">
                    <?php if ($search !== ''): ?>
                        <p>По запросу «<?= htmlspecialchars($search) ?>» ничего не найдено</p>
                    <?php else: ?>
                        <p>История запросов пуста</p>
                    <?php endif; ?>
                </div>
            <?php endif; ?>
        </div>
    </main>
</body>
</html>