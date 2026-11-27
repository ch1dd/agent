<?php
require 'auth.php';
require 'db.php';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    verify_csrf();
    $user_id = $_SESSION['user_id'];
    
    try {
        $stmt = $pdo->prepare("DELETE FROM queries WHERE user_id = ?");
        $stmt->execute([$user_id]);
    } catch (PDOException $e) {
        die("Ошибка очистки: " . $e->getMessage());
    }
}

header('Location: dashboard.php');
exit;