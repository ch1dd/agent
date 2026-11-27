<?php
require 'auth.php';
require 'db.php';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    verify_csrf();
    $question = trim($_POST['question'] ?? '');
    if (mb_strlen($question) > 2000) {
        die('Вопрос слишком длинный (макс. 2000 символов)');
    }
    if (mb_strlen($question) < 3) {
        die('Вопрос слишком короткий');
    }
    $user_id = $_SESSION['user_id'];
    
    if (empty($question)) {
        header('Location: dashboard.php');
        exit;
    }
    
    // Заглушка ответа (позже заменим на реальный RAG)
    $answer = "Это заглушка ответа. Позже здесь будет ответ от ИИ-агента на основе базы знаний. Ваш вопрос был: \"$question\"";
    
    // Сохраняем в БД
    try {
        $stmt = $pdo->prepare("INSERT INTO queries (user_id, question, answer) VALUES (?, ?, ?)");
        $stmt->execute([$user_id, $question, $answer]);
    } catch (PDOException $e) {
        die("Ошибка сохранения: " . $e->getMessage());
    }
}

header('Location: dashboard.php');
exit;