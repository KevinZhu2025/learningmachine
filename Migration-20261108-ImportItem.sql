SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 15000;

IF DB_NAME() <> N'MEMPALACE'
    THROW 51000, 'This migration targets MEMPALACE only.', 1;

IF OBJECT_ID(N'dbo.ImportItem', N'U') IS NULL
   OR OBJECT_ID(N'dbo.ImportBatch', N'U') IS NULL
   OR OBJECT_ID(N'dbo.KnowledgeBase', N'U') IS NULL
    THROW 51001, 'Required tables are missing.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF EXISTS (SELECT 1 FROM dbo.ImportItem WITH (TABLOCKX, HOLDLOCK))
        THROW 51002, 'ImportItem is not empty; review a data backfill before migrating.', 1;

    IF COL_LENGTH(N'dbo.ImportItem', N'SourceType') IS NULL
        ALTER TABLE dbo.ImportItem ADD SourceType NVARCHAR(50) NOT NULL;
    IF OBJECT_ID(N'dbo.CK_ImportItem_SourceType_NotBlank', N'C') IS NOT NULL
        ALTER TABLE dbo.ImportItem DROP CONSTRAINT CK_ImportItem_SourceType_NotBlank;
    ALTER TABLE dbo.ImportItem ALTER COLUMN SourceType NVARCHAR(50) NOT NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'UserID') IS NULL
        ALTER TABLE dbo.ImportItem ADD UserID NVARCHAR(100) NOT NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'Version') IS NULL
        ALTER TABLE dbo.ImportItem ADD Version INT NOT NULL
            CONSTRAINT DF_ImportItem_Version DEFAULT (0) WITH VALUES;
    IF COL_LENGTH(N'dbo.ImportItem', N'AssociatedImage') IS NULL
        ALTER TABLE dbo.ImportItem ADD AssociatedImage NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'QuestionText') IS NULL
        ALTER TABLE dbo.ImportItem ADD QuestionText NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'QuestionType') IS NULL
        ALTER TABLE dbo.ImportItem ADD QuestionType NVARCHAR(100) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'OptionsJson') IS NULL
        ALTER TABLE dbo.ImportItem ADD OptionsJson NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'CorrectAnswer') IS NULL
        ALTER TABLE dbo.ImportItem ADD CorrectAnswer NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'Explanation') IS NULL
        ALTER TABLE dbo.ImportItem ADD Explanation NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'Difficulty') IS NULL
        ALTER TABLE dbo.ImportItem ADD Difficulty NVARCHAR(100) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'QuestionItemsJson') IS NULL
        ALTER TABLE dbo.ImportItem ADD QuestionItemsJson NVARCHAR(MAX) NOT NULL
            CONSTRAINT DF_ImportItem_QuestionItemsJson DEFAULT (N'[]') WITH VALUES;
    IF COL_LENGTH(N'dbo.ImportItem', N'SubjectID') IS NULL
        ALTER TABLE dbo.ImportItem ADD SubjectID NVARCHAR(100) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'ChapterID') IS NULL
        ALTER TABLE dbo.ImportItem ADD ChapterID NVARCHAR(255) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'AIRequestID') IS NULL
        ALTER TABLE dbo.ImportItem ADD AIRequestID VARCHAR(36) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'AIResponseVersion') IS NULL
        ALTER TABLE dbo.ImportItem ADD AIResponseVersion VARCHAR(50) NULL;
    IF COL_LENGTH(N'dbo.ImportItem', N'AIProposal') IS NULL
        ALTER TABLE dbo.ImportItem ADD AIProposal NVARCHAR(MAX) NULL;

    ALTER TABLE dbo.ImportItem ALTER COLUMN KnowledgeTitle NVARCHAR(255) NULL;
    ALTER TABLE dbo.ImportItem ALTER COLUMN CanonicalContent NVARCHAR(MAX) NULL;
    ALTER TABLE dbo.ImportItem ALTER COLUMN Decision VARCHAR(20) NULL;
    ALTER TABLE dbo.ImportItem ALTER COLUMN Confidence FLOAT NULL;
    ALTER TABLE dbo.ImportItem ALTER COLUMN SourceExcerpt NVARCHAR(MAX) NULL;
    ALTER TABLE dbo.ImportItem ALTER COLUMN SourceLocation NVARCHAR(255) NULL;

    IF COL_LENGTH(N'dbo.KnowledgeBase', N'Version') IS NULL
        ALTER TABLE dbo.KnowledgeBase ADD Version INT NOT NULL
            CONSTRAINT DF_KnowledgeBase_Version DEFAULT (0) WITH VALUES;

    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.ImportBatch') AND name=N'UX_ImportBatch_Batch_User')
        CREATE UNIQUE INDEX UX_ImportBatch_Batch_User ON dbo.ImportBatch(ImportBatchID, UserID);

    EXEC sys.sp_executesql N'
    IF OBJECT_ID(N''dbo.FK_ImportItem_Batch_User'', N''F'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT FK_ImportItem_Batch_User
            FOREIGN KEY (ImportBatchID, UserID) REFERENCES dbo.ImportBatch(ImportBatchID, UserID);
    IF OBJECT_ID(N''dbo.CK_ImportItem_SourceType_NotBlank'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_SourceType_NotBlank
            CHECK (LEN(LTRIM(RTRIM(SourceType))) > 0);
    IF OBJECT_ID(N''dbo.CK_ImportItem_Version_NonNegative'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_Version_NonNegative CHECK (Version >= 0);
    IF OBJECT_ID(N''dbo.CK_ImportItem_QuestionItems_Array'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_QuestionItems_Array CHECK (ISJSON(QuestionItemsJson, ARRAY) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportItem_AssociatedImage_Object'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_AssociatedImage_Object
            CHECK (AssociatedImage IS NULL OR ISJSON(AssociatedImage, OBJECT) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportItem_Options_Array'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_Options_Array
            CHECK (OptionsJson IS NULL OR ISJSON(OptionsJson, ARRAY) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportItem_AIProposal_Json'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_AIProposal_Json
            CHECK (AIProposal IS NULL OR ISJSON(AIProposal) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportItem_Staging_Shape'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_Staging_Shape CHECK
        (CASE
            WHEN KnowledgeID IS NOT NULL AND LEN(LTRIM(RTRIM(KnowledgeID))) > 0
             AND KnowledgeTitle IS NOT NULL AND LEN(LTRIM(RTRIM(KnowledgeTitle))) > 0
             AND CanonicalContent IS NOT NULL AND LEN(LTRIM(RTRIM(CanonicalContent))) > 0
             AND Decision IS NOT NULL AND LEN(LTRIM(RTRIM(Decision))) > 0
             AND SourceExcerpt IS NOT NULL AND LEN(LTRIM(RTRIM(SourceExcerpt))) > 0
                THEN 1
            WHEN KnowledgeID IS NULL AND KnowledgeTitle IS NULL AND CanonicalContent IS NULL
             AND Decision IS NULL AND Confidence IS NULL AND SourceExcerpt IS NULL AND SourceLocation IS NULL
             AND SubjectID IS NULL AND ChapterID IS NULL
             AND JSON_VALUE(QuestionItemsJson, ''$[0].QuestionText'') IS NOT NULL
             AND LEN(LTRIM(RTRIM(JSON_VALUE(QuestionItemsJson, ''$[0].QuestionText'')))) > 0
                THEN 1
            ELSE 0
        END = 1);
    IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N''dbo.ImportItem'') AND name=N''UX_ImportItem_ReservedKnowledgeID'')
        CREATE UNIQUE INDEX UX_ImportItem_ReservedKnowledgeID ON dbo.ImportItem(KnowledgeID) WHERE KnowledgeID IS NOT NULL;
    IF OBJECT_ID(N''dbo.CK_KnowledgeBase_Version_NonNegative'', N''C'') IS NULL
        ALTER TABLE dbo.KnowledgeBase WITH CHECK ADD CONSTRAINT CK_KnowledgeBase_Version_NonNegative CHECK (Version >= 0);';

    IF OBJECT_ID(N'dbo.KnowledgeIDSequence', N'SO') IS NULL
    BEGIN
        DECLARE @LargestNumber BIGINT;
        SELECT @LargestNumber=MAX(NumberValue) FROM
        (
            SELECT TRY_CONVERT(BIGINT, KnowledgeID) AS NumberValue FROM dbo.KnowledgeBase
            UNION ALL
            SELECT TRY_CONVERT(BIGINT, KnowledgeID) FROM dbo.ImportItem
        ) AS ExistingNumbers;
        IF @LargestNumber = 9223372036854775807
            THROW 51003, 'Knowledge number range exhausted.', 1;
        DECLARE @FirstNumber BIGINT = CASE WHEN @LargestNumber IS NULL OR @LargestNumber < 1 THEN 1 ELSE @LargestNumber+1 END;
        DECLARE @SequenceSql NVARCHAR(MAX) = N'CREATE SEQUENCE dbo.KnowledgeIDSequence AS BIGINT START WITH '
            + CONVERT(NVARCHAR(30), @FirstNumber) + N' INCREMENT BY 1 MINVALUE 1 NO CYCLE NO CACHE;';
        EXEC sys.sp_executesql @SequenceSql;
    END;

    IF (SELECT COUNT(*) FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.ImportItem')
        AND name IN (N'SourceType', N'UserID', N'Version', N'AssociatedImage', N'QuestionText', N'QuestionType',
                     N'OptionsJson', N'CorrectAnswer', N'Explanation', N'Difficulty', N'QuestionItemsJson',
                     N'SubjectID', N'ChapterID', N'AIRequestID', N'AIResponseVersion', N'AIProposal')) <> 16
        THROW 51004, 'Migration column verification failed.', 1;

    COMMIT TRANSACTION;
    SELECT DB_NAME() AS DatabaseName, N'Migration committed' AS Result,
        (SELECT COUNT_BIG(*) FROM dbo.ImportItem) AS ImportItemCount,
        (SELECT COUNT_BIG(*) FROM dbo.KnowledgeBase) AS KnowledgeBaseCount;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;