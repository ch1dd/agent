<?php
session_start();
$isLoggedIn = isset($_SESSION['user_id']);
$isAdmin = isset($_SESSION['role']) && $_SESSION['role'] === 'admin';
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Agent Demo</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <a href="index.php">AI Agent</a>
        <nav>
            <?php if ($isLoggedIn): ?>
                <?php if ($isAdmin): ?>
                    <a href="admin.php">Админка</a>
                <?php else: ?>
                    <a href="dashboard.php">Кабинет</a>
                <?php endif; ?>
                <a href="logout.php">Выйти</a>
            <?php else: ?>
                <a href="login.php">Войти</a>
                <a href="register.php">Регистрация</a>
            <?php endif; ?>
        </nav>
    </header>

    <main>
        <section class="hero">
            <h1>Демонстрация ИИ-агента</h1>
            <p>Задавайте вопросы и получайте ответы на основе базы знаний</p>
            <div class="hero-buttons">
                <?php if (!$isLoggedIn): ?>
                    <a href="register.php" class="btn btn-primary">Начать</a>
                    <a href="login.php" class="btn btn-secondary">Войти</a>
                <?php else: ?>
                    <a href="dashboard.php" class="btn btn-primary">Перейти к агенту</a>
                <?php endif; ?>
            </div>
        </section>

        <section class="features">
            <div class="feature-card">
                <h3>💬 Умные ответы</h3>
                <p>Агент отвечает на вопросы, используя загруженные документы и базу знаний</p>
            </div>
            <div class="feature-card">
                <h3>📚 База знаний</h3>
                <p>Администратор может загружать документы и обновлять знания агента</p>
            </div>
            <div class="feature-card">
                <h3>🔒 Безопасность</h3>
                <p>Разграничение доступа: пользователи и администраторы</p>
            </div>
        </section>
    </main>
</body>
</html>