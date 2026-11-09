SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 15000;
IF DB_NAME() <> N'MEMPALACE' THROW 51000, 'Wrong database.', 1;
BEGIN TRY
    BEGIN TRANSACTION;
    IF COL_LENGTH(N'dbo.ImportItem', N'VersionNo') IS NULL
        ALTER TABLE dbo.ImportItem ADD VersionNo INT NOT NULL CONSTRAINT DF_ImportItem_VersionNo DEFAULT (1) WITH VALUES;
    IF COL_LENGTH(N'dbo.ImportBatch', N'PromptHistorySnapshotJson') IS NULL
        ALTER TABLE dbo.ImportBatch ADD PromptHistorySnapshotJson NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportBatch', N'PromptSnapshotVersion') IS NULL
        ALTER TABLE dbo.ImportBatch ADD PromptSnapshotVersion INT NOT NULL CONSTRAINT DF_ImportBatch_PromptSnapshotVersion DEFAULT (1) WITH VALUES;
    IF COL_LENGTH(N'dbo.ImportBatch', N'ModelRequestJson') IS NULL
        ALTER TABLE dbo.ImportBatch ADD ModelRequestJson NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportBatch', N'ModelRequestObjectKey') IS NULL
        ALTER TABLE dbo.ImportBatch ADD ModelRequestObjectKey VARCHAR(512) NULL;
    IF COL_LENGTH(N'dbo.ImportBatch', N'ModelResponseObjectKey') IS NULL
        ALTER TABLE dbo.ImportBatch ADD ModelResponseObjectKey VARCHAR(512) NULL;
    IF COL_LENGTH(N'dbo.ImportBatch', N'AuditContainerName') IS NULL
        ALTER TABLE dbo.ImportBatch ADD AuditContainerName VARCHAR(63) NULL;
    IF COL_LENGTH(N'dbo.ImportSource', N'ExtractedSegmentsJson') IS NULL
        ALTER TABLE dbo.ImportSource ADD ExtractedSegmentsJson NVARCHAR(MAX) NULL;
    IF COL_LENGTH(N'dbo.ImportSource', N'ExtractionVersion') IS NULL
        ALTER TABLE dbo.ImportSource ADD ExtractionVersion INT NOT NULL CONSTRAINT DF_ImportSource_ExtractionVersion DEFAULT (1) WITH VALUES;
    IF COL_LENGTH(N'dbo.ImportSource', N'OcrResultObjectKey') IS NULL
        ALTER TABLE dbo.ImportSource ADD OcrResultObjectKey VARCHAR(512) NULL;
    IF COL_LENGTH(N'dbo.ImportSource', N'OcrResultContainerName') IS NULL
        ALTER TABLE dbo.ImportSource ADD OcrResultContainerName VARCHAR(63) NULL;
    ALTER TABLE dbo.ImportSource ALTER COLUMN ContainerName VARCHAR(63) NOT NULL;
    EXEC sys.sp_executesql N'
    IF OBJECT_ID(N''dbo.CK_ImportItem_VersionNo'', N''C'') IS NULL
        ALTER TABLE dbo.ImportItem WITH CHECK ADD CONSTRAINT CK_ImportItem_VersionNo CHECK (VersionNo >= 1);
    IF OBJECT_ID(N''dbo.CK_ImportBatch_RequestJson'', N''C'') IS NULL
        ALTER TABLE dbo.ImportBatch WITH CHECK ADD CONSTRAINT CK_ImportBatch_RequestJson CHECK (ModelRequestJson IS NULL OR ISJSON(ModelRequestJson, OBJECT) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportBatch_HistoryJson'', N''C'') IS NULL
        ALTER TABLE dbo.ImportBatch WITH CHECK ADD CONSTRAINT CK_ImportBatch_HistoryJson CHECK (PromptHistorySnapshotJson IS NULL OR ISJSON(PromptHistorySnapshotJson, ARRAY) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportBatch_SnapshotVersion'', N''C'') IS NULL
        ALTER TABLE dbo.ImportBatch WITH CHECK ADD CONSTRAINT CK_ImportBatch_SnapshotVersion CHECK (PromptSnapshotVersion >= 1);
    IF OBJECT_ID(N''dbo.CK_ImportSource_SegmentsJson'', N''C'') IS NULL
        ALTER TABLE dbo.ImportSource WITH CHECK ADD CONSTRAINT CK_ImportSource_SegmentsJson CHECK (ExtractedSegmentsJson IS NULL OR ISJSON(ExtractedSegmentsJson, ARRAY) = 1);
    IF OBJECT_ID(N''dbo.CK_ImportSource_ExtractionVersion'', N''C'') IS NULL
        ALTER TABLE dbo.ImportSource WITH CHECK ADD CONSTRAINT CK_ImportSource_ExtractionVersion CHECK (ExtractionVersion >= 1);';
    COMMIT TRANSACTION;
    SELECT DB_NAME() AS DatabaseName, N'Import pipeline schema committed' AS Result;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;