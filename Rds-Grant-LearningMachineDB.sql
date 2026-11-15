/*
    RDS SQL Server 应用账号最小权限授权脚本（在 LearningMachineDB 内执行）。
    使用方式：
      1. 先在 RDS 控制台创建高权限账号，并创建数据库 LearningMachineDB；
      2. 用高权限账号通过 DMS/SSMS 连接到 LearningMachineDB；
      3. 创建应用登录名（替换 @app_password 为强密码），再执行本脚本授予最小权限。
    权限边界沿用原 Azure 最小授权模型：
      - 认证/限流/提示词/导入相关表读写；
      - 正式 KnowledgeBase 只读、QuestionBank 不授权；
      - 仅允许使用 KnowledgeIDSequence 取号，不授予 db_owner。
*/

/* 创建应用账号（登录名与用户名均为 learningmachine_app，按需修改） */
IF SUSER_ID(N'learningmachine_app') IS NULL
BEGIN
    CREATE LOGIN learningmachine_app
        WITH PASSWORD = N'替换为强密码',
             CHECK_POLICY = ON,
             CHECK_EXPIRATION = OFF;
END;

IF USER_ID(N'learningmachine_app') IS NULL
    CREATE USER learningmachine_app FOR LOGIN learningmachine_app;

/* 连接与当前库访问基线 */
GRANT CONNECT TO learningmachine_app;

/* 导入与认证相关业务表：读写 */
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.UserProfile TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.AuthSession TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.AuthThrottle TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.ImportBatch TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.ImportSource TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.ImportItem TO learningmachine_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.PromptLeading TO learningmachine_app;

/* 正式知识库只读（应用首期不写正式知识） */
GRANT SELECT ON dbo.KnowledgeBase TO learningmachine_app;
DENY INSERT, UPDATE, DELETE ON dbo.KnowledgeBase TO learningmachine_app;

/* 题库不授权（保持隐式拒绝，不写 DENY 以免影响未来策略调整） */

/* 知识编号序列：仅取号权限 */
GRANT CONTROL ON OBJECT::dbo.KnowledgeIDSequence TO learningmachine_app;
