/*
    Azure SQL Database / SQL Server DDL for the worksheets in 数据库MOCKUP.xlsx.
    Run this script in the target database. Existing tables are left unchanged.

    Schema decisions:
    - KnowledgeTrace contains two ChapterID headers in the workbook; SQL column
      names must be unique, so the duplicate is represented once.
    - UserProfile.Password is represented as PasswordHash. Never store plaintext
      passwords in this column.
    - PromptLeading allows multiple records per course/user; Creation_Time and
      PromptLeadingID support deterministic newest-first retrieval.
    - The workbook's sample rows are not inserted by this DDL.
*/

SET NOCOUNT ON;
GO

IF OBJECT_ID(N'dbo.UserProfile', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.UserProfile
    (
        UserID       NVARCHAR(100) NOT NULL,
        PasswordHash NVARCHAR(512) NOT NULL,
        CONSTRAINT PK_UserProfile PRIMARY KEY CLUSTERED (UserID)
    );
END;
GO

IF OBJECT_ID(N'dbo.KnowledgeBase', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.KnowledgeBase
    (
        KnowledgeID        NVARCHAR(50)  NOT NULL,
        KnowledgeTitle     NVARCHAR(255) NOT NULL,
        CanonicalContent   NVARCHAR(MAX) NOT NULL,
        SubjectID          NVARCHAR(100) NULL,
        ChapterID          NVARCHAR(255) NULL,
        AssociatedImagines NVARCHAR(MAX) NULL,
        Tags               NVARCHAR(MAX) NULL,
        ExamHeat           INT           NOT NULL
            CONSTRAINT DF_KnowledgeBase_ExamHeat DEFAULT (0),
        UserID             NVARCHAR(100) NOT NULL,
        CONSTRAINT PK_KnowledgeBase PRIMARY KEY CLUSTERED (KnowledgeID),
        CONSTRAINT CK_KnowledgeBase_ExamHeat_NonNegative CHECK (ExamHeat >= 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.KnowledgeTrace', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.KnowledgeTrace
    (
        TraceID               BIGINT IDENTITY(1,1) NOT NULL,
        KnowledgeID           NVARCHAR(50)  NOT NULL,
        KnowledgeContentsAdded NVARCHAR(MAX) NULL,
        ChangeType            VARCHAR(20)   NOT NULL,
        KnowledgeSource       NVARCHAR(2048) NULL,
        SubjectID             NVARCHAR(100) NULL,
        ChapterID             NVARCHAR(255) NULL,
        HumanApproval         BIT           NULL,
        AI_Eval_Returned      NVARCHAR(MAX) NULL,
        SourceType            VARCHAR(20)   NOT NULL,
        ImportBatchID         NVARCHAR(100) NULL,
        UserID                NVARCHAR(100) NOT NULL,
        UserComments          NVARCHAR(MAX) NULL,
        CreatedAt             DATETIME2(7)  NOT NULL
            CONSTRAINT DF_KnowledgeTrace_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_KnowledgeTrace PRIMARY KEY CLUSTERED (TraceID),
        CONSTRAINT FK_KnowledgeTrace_KnowledgeBase
            FOREIGN KEY (KnowledgeID) REFERENCES dbo.KnowledgeBase (KnowledgeID),
        CONSTRAINT CK_KnowledgeTrace_ChangeType
            CHECK (ChangeType IN ('CREATE', 'SUPPLEMENT', 'EDIT', 'MERGE')),
        CONSTRAINT CK_KnowledgeTrace_SourceType
            CHECK (SourceType IN ('BLOB', 'WEB', 'EXAM', 'MANUAL'))
    );
END;
GO

IF OBJECT_ID(N'dbo.QuestionBank', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.QuestionBank
    (
        QuestionID       BIGINT IDENTITY(1,1) NOT NULL,
        QuestionText     NVARCHAR(MAX) NOT NULL,
        QuestionType     NVARCHAR(30)  NULL,
        OptionsJson      NVARCHAR(MAX) NULL,
        CorrectAnswer    NVARCHAR(MAX) NULL,
        Explanation      NVARCHAR(MAX) NULL,
        Difficulty       NVARCHAR(50)  NULL,
        SubjectID        NVARCHAR(100) NULL,
        ChapterID        NVARCHAR(255) NULL,
        KnowledgeID      NVARCHAR(50)  NULL,
        KnowledgeContent NVARCHAR(MAX) NULL,
        HumanApproval    BIT           NULL,
        SourceURL        NVARCHAR(2048) NULL,
        OccurrenceCount  INT           NOT NULL
            CONSTRAINT DF_QuestionBank_OccurrenceCount DEFAULT (1),
        Status           NVARCHAR(30)  NULL,
        UserID           NVARCHAR(100) NOT NULL,
        UserComments     NVARCHAR(MAX) NULL,
        CONSTRAINT PK_QuestionBank PRIMARY KEY CLUSTERED (QuestionID),
        CONSTRAINT FK_QuestionBank_KnowledgeBase
            FOREIGN KEY (KnowledgeID) REFERENCES dbo.KnowledgeBase (KnowledgeID),
        CONSTRAINT CK_QuestionBank_OccurrenceCount_Positive CHECK (OccurrenceCount > 0),
        CONSTRAINT CK_QuestionBank_OptionsJson
            CHECK (OptionsJson IS NULL OR ISJSON(OptionsJson) = 1)
    );
END;
GO

IF OBJECT_ID(N'dbo.PromptLeading', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.PromptLeading
    (
        PromptLeadingID BIGINT IDENTITY(1,1) NOT NULL,
        SubjectID       NVARCHAR(100) NOT NULL,
        Prompts         NVARCHAR(MAX) NOT NULL,
        UserID          NVARCHAR(100) NULL,
        Creation_Time   DATETIME2(7) NOT NULL
            CONSTRAINT DF_PromptLeading_Creation_Time DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_PromptLeading PRIMARY KEY CLUSTERED (PromptLeadingID)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
      AND name = N'IX_PromptLeading_Subject_User_Creation'
)
BEGIN
    CREATE INDEX IX_PromptLeading_Subject_User_Creation
        ON dbo.PromptLeading (SubjectID, UserID, Creation_Time DESC, PromptLeadingID DESC);
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.KnowledgeTrace')
      AND name = N'IX_KnowledgeTrace_Knowledge_CreatedAt'
)
BEGIN
    CREATE INDEX IX_KnowledgeTrace_Knowledge_CreatedAt
        ON dbo.KnowledgeTrace (KnowledgeID, CreatedAt DESC);
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.QuestionBank')
      AND name = N'IX_QuestionBank_KnowledgeID'
)
BEGIN
    CREATE INDEX IX_QuestionBank_KnowledgeID
        ON dbo.QuestionBank (KnowledgeID)
        WHERE KnowledgeID IS NOT NULL;
END;
GO