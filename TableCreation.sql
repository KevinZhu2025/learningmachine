/*
    SQL Server / Azure SQL schema creation and migration script.
    Existing import and knowledge rows are preserved. The approved migration at
    the end drops KnowledgeTrace and ImportBatch.SourceObjectKey.
*/

SET NOCOUNT ON;
GO

IF OBJECT_ID(N'dbo.UserRole', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.UserRole
    (
        Role                VARCHAR(36) NOT NULL,
        Class_Hour_Threshold INT NOT NULL
            CONSTRAINT DF_UserRole_Class_Hour_Threshold DEFAULT (0),
        Medal_Count         INT NOT NULL
            CONSTRAINT DF_UserRole_Medal_Count DEFAULT (0),
        Medal_Type          VARCHAR(36) NOT NULL,
        Medal_Image         VARBINARY(MAX) NULL,
        CONSTRAINT PK_UserRole PRIMARY KEY CLUSTERED (Role),
        CONSTRAINT CK_UserRole_Class_Hour_Threshold CHECK (Class_Hour_Threshold >= 0),
        CONSTRAINT CK_UserRole_Medal_Count CHECK (Medal_Count >= 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.UserProfile', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.UserProfile
    (
        UserID       NVARCHAR(100) NOT NULL,
        PasswordHash NVARCHAR(512) NOT NULL,
        Role         VARCHAR(50) NULL,
        Class_Hour   INT NULL,
        CONSTRAINT PK_UserProfile PRIMARY KEY CLUSTERED (UserID)
    );
END;
GO

IF COL_LENGTH(N'dbo.UserProfile', N'Role') IS NULL
    ALTER TABLE dbo.UserProfile ADD Role VARCHAR(50) NULL;
GO

IF COL_LENGTH(N'dbo.UserProfile', N'Class_Hour') IS NULL
    ALTER TABLE dbo.UserProfile ADD Class_Hour INT NULL;
GO

IF OBJECT_ID(N'dbo.AuthSession', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthSession
    (
        SessionTokenHash CHAR(64) NOT NULL,
        UserID           NVARCHAR(100) NOT NULL,
        CreatedAt        DATETIME2 NOT NULL,
        ExpiresAt        DATETIME2 NOT NULL,
        CONSTRAINT PK_AuthSession PRIMARY KEY CLUSTERED (SessionTokenHash),
        CONSTRAINT FK_AuthSession_UserProfile FOREIGN KEY (UserID)
            REFERENCES dbo.UserProfile (UserID)
    );
    CREATE INDEX ix_AuthSession_UserID ON dbo.AuthSession (UserID);
    CREATE INDEX ix_AuthSession_ExpiresAt ON dbo.AuthSession (ExpiresAt);
END;
GO

IF OBJECT_ID(N'dbo.AuthThrottle', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.AuthThrottle
    (
        RateLimitKey   CHAR(64) NOT NULL,
        AttemptCount   INT NOT NULL,
        WindowStartedAt DATETIME2 NOT NULL,
        CONSTRAINT PK_AuthThrottle PRIMARY KEY CLUSTERED (RateLimitKey),
        CONSTRAINT CK_AuthThrottle_AttemptCount CHECK (AttemptCount > 0)
    );
END;
GO

IF OBJECT_ID(N'dbo.KnowledgeBase', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.KnowledgeBase
    (
        KnowledgeID      NVARCHAR(50)  NOT NULL,
        KnowledgeTitle   NVARCHAR(255) NOT NULL,
        CanonicalContent NVARCHAR(MAX) NOT NULL,
        SubjectID        NVARCHAR(100) NULL,
        ChapterID        NVARCHAR(255) NULL,
        Tags             NVARCHAR(MAX) NULL,
        ExamHeat         INT NOT NULL
            CONSTRAINT DF_KnowledgeBase_ExamHeat DEFAULT (0),
        UserID           NVARCHAR(100) NOT NULL,
        ImportBatchID    VARCHAR(36) NULL,
        ItemID           VARCHAR(36) NULL,
        CONSTRAINT PK_KnowledgeBase PRIMARY KEY CLUSTERED (KnowledgeID),
        CONSTRAINT CK_KnowledgeBase_ExamHeat_NonNegative CHECK (ExamHeat >= 0)
    );
END;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'AssociatedImages') IS NOT NULL
    ALTER TABLE dbo.KnowledgeBase DROP COLUMN AssociatedImages;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'AssociatedImagines') IS NOT NULL
    ALTER TABLE dbo.KnowledgeBase DROP COLUMN AssociatedImagines;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'ImportBatchID') IS NULL
    ALTER TABLE dbo.KnowledgeBase ADD ImportBatchID VARCHAR(36) NULL;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'ItemID') IS NULL
    ALTER TABLE dbo.KnowledgeBase ADD ItemID VARCHAR(36) NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.KnowledgeBase')
      AND name = N'UX_KnowledgeBase_ItemID'
)
BEGIN
    CREATE UNIQUE INDEX UX_KnowledgeBase_ItemID
        ON dbo.KnowledgeBase (ItemID)
        WHERE ItemID IS NOT NULL;
END;
GO

IF OBJECT_ID(N'dbo.QuestionBank', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.QuestionBank
    (
        QuestionID       BIGINT IDENTITY(1,1) NOT NULL,
        QuestionText     NVARCHAR(MAX) NOT NULL,
        QuestionType     NVARCHAR(30) NULL,
        OptionsJson      NVARCHAR(MAX) NULL,
        CorrectAnswer    NVARCHAR(MAX) NULL,
        Explanation      NVARCHAR(MAX) NULL,
        Difficulty       NVARCHAR(50) NULL,
        SubjectID        NVARCHAR(100) NULL,
        ChapterID        NVARCHAR(255) NULL,
        KnowledgeID      NVARCHAR(50) NULL,
        KnowledgeContent NVARCHAR(MAX) NULL,
        HumanApproval    BIT NULL,
        OccurrenceCount  INT NOT NULL
            CONSTRAINT DF_QuestionBank_OccurrenceCount DEFAULT (1),
        UserID           NVARCHAR(100) NOT NULL,
        ImportBatchID    VARCHAR(36) NULL,
        ItemID           VARCHAR(36) NULL,
        CONSTRAINT PK_QuestionBank PRIMARY KEY CLUSTERED (QuestionID),
        CONSTRAINT FK_QuestionBank_KnowledgeBase
            FOREIGN KEY (KnowledgeID) REFERENCES dbo.KnowledgeBase (KnowledgeID),
        CONSTRAINT CK_QuestionBank_OccurrenceCount_Positive CHECK (OccurrenceCount > 0),
        CONSTRAINT CK_QuestionBank_OptionsJson
            CHECK (OptionsJson IS NULL OR ISJSON(OptionsJson) = 1)
    );
END;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'SourceURL') IS NOT NULL
    ALTER TABLE dbo.QuestionBank DROP COLUMN SourceURL;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'Status') IS NOT NULL
    ALTER TABLE dbo.QuestionBank DROP COLUMN Status;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'UserComments') IS NOT NULL
    ALTER TABLE dbo.QuestionBank DROP COLUMN UserComments;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'ImportBatchID') IS NULL
    ALTER TABLE dbo.QuestionBank ADD ImportBatchID VARCHAR(36) NULL;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'ItemID') IS NULL
    ALTER TABLE dbo.QuestionBank ADD ItemID VARCHAR(36) NULL;
GO

IF OBJECT_ID(N'dbo.PromptLeading', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.PromptLeading
    (
        Prompts_SN    VARCHAR(36) NOT NULL,
        SubjectID     NVARCHAR(100) NOT NULL,
        Prompts       NVARCHAR(MAX) NOT NULL,
        UserID        NVARCHAR(100) NULL,
        Creation_Time DATETIME2(7) NOT NULL
            CONSTRAINT DF_PromptLeading_Creation_Time DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_PromptLeading PRIMARY KEY CLUSTERED (Prompts_SN)
    );
END;
GO

IF COL_LENGTH(N'dbo.PromptLeading', N'Prompts_SN') IS NULL
    ALTER TABLE dbo.PromptLeading ADD Prompts_SN VARCHAR(36) NULL;
GO

IF COL_LENGTH(N'dbo.PromptLeading', N'Prompts_SN') IS NOT NULL
   AND EXISTS
   (
       SELECT 1
       FROM dbo.PromptLeading
       WHERE Prompts_SN IS NULL
   )
BEGIN
    UPDATE dbo.PromptLeading SET Prompts_SN = CONVERT(VARCHAR(36), NEWID()) WHERE Prompts_SN IS NULL;
END;
GO

IF COL_LENGTH(N'dbo.PromptLeading', N'Prompts_SN') IS NOT NULL
   AND EXISTS
   (
       SELECT 1
       FROM sys.columns
       WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
         AND name = N'Prompts_SN'
         AND is_nullable = 1
   )
    ALTER TABLE dbo.PromptLeading ALTER COLUMN Prompts_SN VARCHAR(36) NOT NULL;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
      AND name = N'UX_PromptLeading_Prompts_SN'
)
BEGIN
    CREATE UNIQUE INDEX UX_PromptLeading_Prompts_SN
        ON dbo.PromptLeading (Prompts_SN);
END;
GO

IF OBJECT_ID(N'dbo.ImportBatch', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ImportBatch
    (
        ImportBatchID       VARCHAR(36) NOT NULL,
        UserID              NVARCHAR(100) NOT NULL,
        IdempotencyKey      VARCHAR(128) NULL,
        RequestFingerprint  CHAR(64) NULL,
        SourceName          NVARCHAR(255) NOT NULL,
        SourceType          VARCHAR(20) NOT NULL,
        SubjectID           NVARCHAR(100) NULL,
        UserComments        NVARCHAR(MAX) NULL,
        Status              VARCHAR(20) NOT NULL,
        ErrorMessage        NVARCHAR(MAX) NULL,
        IsPreview           BIT NOT NULL
            CONSTRAINT DF_ImportBatch_IsPreview DEFAULT (1),
        PromptSnapshot      NVARCHAR(MAX) NULL,
        PromptSnapshotSource VARCHAR(50) NULL,
        PromptSnapshotIds   NVARCHAR(MAX) NULL,
        CreatedAt           DATETIME2(7) NOT NULL
            CONSTRAINT DF_ImportBatch_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_ImportBatch PRIMARY KEY CLUSTERED (ImportBatchID)
    );
END;
GO

IF COL_LENGTH(N'dbo.ImportBatch', N'IdempotencyKey') IS NULL
    ALTER TABLE dbo.ImportBatch ADD IdempotencyKey VARCHAR(128) NULL;
GO

IF COL_LENGTH(N'dbo.ImportBatch', N'RequestFingerprint') IS NULL
    ALTER TABLE dbo.ImportBatch ADD RequestFingerprint CHAR(64) NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportBatch')
      AND name = N'UX_ImportBatch_User_Idempotency'
)
BEGIN
    CREATE UNIQUE INDEX UX_ImportBatch_User_Idempotency
        ON dbo.ImportBatch (UserID, IdempotencyKey)
        WHERE IdempotencyKey IS NOT NULL;
END;
GO

IF OBJECT_ID(N'dbo.ImportSource', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ImportSource
    (
        SourceID          VARCHAR(36) NOT NULL,
        ImportBatchID     VARCHAR(36) NOT NULL,
        SourceType        VARCHAR(20) NOT NULL
            CONSTRAINT DF_ImportSource_SourceType DEFAULT ('BLOB'),
        SourceName        NVARCHAR(255) NOT NULL,
        SourceURL         NVARCHAR(2048) NULL,
        ContainerName     VARCHAR(36) NOT NULL,
        SourceObjectKey   VARCHAR(512) NOT NULL,
        SourceFileName    NVARCHAR(255) NOT NULL,
        SourceContentType NVARCHAR(255) NULL,
        SourceSizeBytes   BIGINT NULL,
        SourceSHA256      VARCHAR(64) NULL,
        CreatedAt         DATETIME2(7) NOT NULL
            CONSTRAINT DF_ImportSource_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_ImportSource PRIMARY KEY CLUSTERED (SourceID),
        CONSTRAINT UQ_ImportSource_Batch_Source UNIQUE (ImportBatchID, SourceID),
        CONSTRAINT FK_ImportSource_ImportBatch
            FOREIGN KEY (ImportBatchID) REFERENCES dbo.ImportBatch (ImportBatchID)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportSource')
      AND name = N'UX_ImportSource_Container_ObjectKey'
)
BEGIN
    CREATE UNIQUE INDEX UX_ImportSource_Container_ObjectKey
        ON dbo.ImportSource (ContainerName, SourceObjectKey);
END;
GO

IF OBJECT_ID(N'dbo.ImportItem', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ImportItem
    (
        ItemID          VARCHAR(36) NOT NULL,
        ImportBatchID   VARCHAR(36) NOT NULL,
        SourceID        VARCHAR(36) NOT NULL,
        KnowledgeID     NVARCHAR(50) NULL,
        KnowledgeTitle  NVARCHAR(255) NOT NULL,
        CanonicalContent NVARCHAR(MAX) NOT NULL,
        Decision        VARCHAR(20) NOT NULL,
        Confidence      FLOAT NOT NULL,
        SourceExcerpt   NVARCHAR(MAX) NOT NULL,
        SourceLocation  NVARCHAR(255) NOT NULL,
        SubjectID       NVARCHAR(100) NULL,
        ChapterID       NVARCHAR(255) NULL,
        HumanApproval   VARCHAR(20) NOT NULL
            CONSTRAINT DF_ImportItem_HumanApproval DEFAULT ('PENDING'),
        VersionNo       INT NOT NULL
            CONSTRAINT DF_ImportItem_VersionNo DEFAULT (1),
        CreatedAt       DATETIME2(7) NOT NULL
            CONSTRAINT DF_ImportItem_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_ImportItem PRIMARY KEY CLUSTERED (ItemID),
        CONSTRAINT FK_ImportItem_ImportBatch
            FOREIGN KEY (ImportBatchID) REFERENCES dbo.ImportBatch (ImportBatchID),
        CONSTRAINT FK_ImportItem_ImportSource
            FOREIGN KEY (ImportBatchID, SourceID)
            REFERENCES dbo.ImportSource (ImportBatchID, SourceID),
        CONSTRAINT CK_ImportItem_Confidence CHECK (Confidence >= 0 AND Confidence <= 1)
    );
END;
GO

IF COL_LENGTH(N'dbo.ImportItem', N'SourceID') IS NULL
    ALTER TABLE dbo.ImportItem ADD SourceID VARCHAR(36) NULL;
GO

IF COL_LENGTH(N'dbo.ImportItem', N'SubjectID') IS NULL
    ALTER TABLE dbo.ImportItem ADD SubjectID NVARCHAR(100) NULL;
GO

IF COL_LENGTH(N'dbo.ImportItem', N'ChapterID') IS NULL
    ALTER TABLE dbo.ImportItem ADD ChapterID NVARCHAR(255) NULL;
GO

IF COL_LENGTH(N'dbo.ImportItem', N'VersionNo') IS NULL
    ALTER TABLE dbo.ImportItem ADD VersionNo INT NOT NULL
        CONSTRAINT DF_ImportItem_VersionNo DEFAULT (1);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_ImportItem_ImportBatch')
BEGIN
    ALTER TABLE dbo.ImportItem WITH CHECK
        ADD CONSTRAINT FK_ImportItem_ImportBatch
        FOREIGN KEY (ImportBatchID) REFERENCES dbo.ImportBatch (ImportBatchID);
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_ImportItem_ImportSource')
BEGIN
    ALTER TABLE dbo.ImportItem WITH CHECK
        ADD CONSTRAINT FK_ImportItem_ImportSource
        FOREIGN KEY (ImportBatchID, SourceID)
        REFERENCES dbo.ImportSource (ImportBatchID, SourceID);
END;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'ImportBatchID') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_KnowledgeBase_ImportBatch')
BEGIN
    ALTER TABLE dbo.KnowledgeBase WITH CHECK
        ADD CONSTRAINT FK_KnowledgeBase_ImportBatch
        FOREIGN KEY (ImportBatchID) REFERENCES dbo.ImportBatch (ImportBatchID);
END;
GO

IF COL_LENGTH(N'dbo.KnowledgeBase', N'ItemID') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_KnowledgeBase_ImportItem')
BEGIN
    ALTER TABLE dbo.KnowledgeBase WITH CHECK
        ADD CONSTRAINT FK_KnowledgeBase_ImportItem
        FOREIGN KEY (ItemID) REFERENCES dbo.ImportItem (ItemID);
END;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'ImportBatchID') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_QuestionBank_ImportBatch')
BEGIN
    ALTER TABLE dbo.QuestionBank WITH CHECK
        ADD CONSTRAINT FK_QuestionBank_ImportBatch
        FOREIGN KEY (ImportBatchID) REFERENCES dbo.ImportBatch (ImportBatchID);
END;
GO

IF COL_LENGTH(N'dbo.QuestionBank', N'ItemID') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_QuestionBank_ImportItem')
BEGIN
    ALTER TABLE dbo.QuestionBank WITH CHECK
        ADD CONSTRAINT FK_QuestionBank_ImportItem
        FOREIGN KEY (ItemID) REFERENCES dbo.ImportItem (ItemID);
END;
GO

IF EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
      AND name = N'IX_PromptLeading_Subject_User_Creation'
)
AND
(
    (
        SELECT COUNT(*)
        FROM sys.index_columns
        WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
          AND index_id = INDEXPROPERTY(
              OBJECT_ID(N'dbo.PromptLeading'),
              N'IX_PromptLeading_Subject_User_Creation',
              'IndexID'
          )
          AND key_ordinal > 0
    ) <> 4
    OR EXISTS
(
    SELECT 1
    FROM (VALUES
        (1, N'SubjectID'),
        (2, N'UserID'),
        (3, N'Creation_Time'),
        (4, N'Prompts_SN')
    ) AS expected(key_ordinal, column_name)
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM sys.index_columns AS ic
        JOIN sys.columns AS c
            ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = OBJECT_ID(N'dbo.PromptLeading')
          AND ic.index_id = INDEXPROPERTY(
              OBJECT_ID(N'dbo.PromptLeading'),
              N'IX_PromptLeading_Subject_User_Creation',
              'IndexID'
          )
          AND ic.key_ordinal = expected.key_ordinal
          AND c.name = expected.column_name
    )
)
)
BEGIN
    DROP INDEX IX_PromptLeading_Subject_User_Creation ON dbo.PromptLeading;
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.PromptLeading')
      AND name = N'IX_PromptLeading_Subject_User_Creation'
)
BEGIN
    CREATE INDEX IX_PromptLeading_Subject_User_Creation
        ON dbo.PromptLeading (SubjectID, UserID, Creation_Time DESC, Prompts_SN DESC);
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportBatch')
      AND name = N'IX_ImportBatch_User_CreatedAt'
)
BEGIN
    CREATE INDEX IX_ImportBatch_User_CreatedAt
        ON dbo.ImportBatch (UserID, CreatedAt DESC);
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportBatch')
      AND name = N'IX_ImportBatch_Status'
)
BEGIN
    CREATE INDEX IX_ImportBatch_Status ON dbo.ImportBatch (Status);
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportItem')
      AND name = N'IX_ImportItem_ImportBatchID'
)
BEGIN
    CREATE INDEX IX_ImportItem_ImportBatchID ON dbo.ImportItem (ImportBatchID);
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.ImportItem')
      AND name = N'IX_ImportItem_SourceID'
)
BEGIN
    CREATE INDEX IX_ImportItem_SourceID ON dbo.ImportItem (SourceID) WHERE SourceID IS NOT NULL;
END;
GO

IF OBJECT_ID(N'dbo.KnowledgeTrace', N'U') IS NOT NULL
BEGIN
    IF EXISTS
    (
        SELECT 1
        FROM sys.foreign_keys
        WHERE referenced_object_id = OBJECT_ID(N'dbo.KnowledgeTrace')
    )
    BEGIN
        ;THROW 51000, 'Cannot drop dbo.KnowledgeTrace while foreign keys reference it; resolve dependent relationships first.', 1;
    END;

    DROP TABLE dbo.KnowledgeTrace;
END;
GO

IF COL_LENGTH(N'dbo.ImportBatch', N'SourceObjectKey') IS NOT NULL
BEGIN
    ALTER TABLE dbo.ImportBatch DROP COLUMN SourceObjectKey;
END;
GO
